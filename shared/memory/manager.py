"""Conversation memory manager using the summarize-only strategy.

Per architecture decision: store a rolling summary + last N messages.
Only the summary (not the full transcript) is exposed back to the LLM,
which keeps prompts short while preserving continuity across long sessions.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.llm_adapter import LLMAdapter
from shared.storage import portable_sql

SUMMARIZE_PROMPT = """You are maintaining a running summary of a conversation
between a user and an AI agent.

Existing summary (may be empty on first turn):
---
{prior_summary}
---

New messages since the last summary:
---
{new_messages}
---

Produce an UPDATED summary that:
- Preserves all decisions, names, identifiers, file paths, project IDs
- Captures unresolved questions and pending actions
- Drops verbatim chit-chat, greetings, and resolved tangents
- Stays under 400 words
Return ONLY the updated summary, no preamble."""


@dataclass
class MemoryConfig:
    recent_window: int = 3        # how many recent messages to keep in-context
    summarize_every: int = 5      # rebuild summary every N new messages
    trim_after: int = 20          # hard cap on recent_messages rows kept per conv

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryConfig":
        return cls(
            recent_window=int(d.get("recent_window", 3)),
            summarize_every=int(d.get("summarize_every", 5)),
            trim_after=int(d.get("trim_after", 20)),
        )


class MemoryManager:
    """Async memory manager backed by Postgres."""

    def __init__(self, session: AsyncSession, llm: LLMAdapter, cfg: MemoryConfig | None = None):
        self.session = session
        self.llm = llm
        self.cfg = cfg or MemoryConfig()

    # ------------------------------------------------------------------
    # Conversation lifecycle
    # ------------------------------------------------------------------
    async def get_or_create_conversation(
        self,
        *,
        agent_name: str,
        scope_key: str | None = None,
        user_id: str | None = None,
        title: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        if conversation_id:
            row = await self.session.execute(
                text("SELECT id FROM conversations WHERE id = :id"),
                {"id": conversation_id},
            )
            if row.first():
                return conversation_id

        new_id = conversation_id or str(uuid.uuid4())
        await self.session.execute(
            text(
                """
                INSERT INTO conversations (id, agent_name, scope_key, user_id, title)
                VALUES (:id, :agent, :scope, :user, :title)
                """
            ),
            {"id": new_id, "agent": agent_name, "scope": scope_key, "user": user_id, "title": title},
        )
        await self.session.execute(
            text(
                "INSERT INTO conversation_summaries (conversation_id, running_summary, message_count) "
                "VALUES (:id, '', 0)"
            ),
            {"id": new_id},
        )
        await self.session.commit()
        return new_id

    # ------------------------------------------------------------------
    # Read context for the agent
    # ------------------------------------------------------------------
    async def get_context(self, conversation_id: str) -> dict[str, Any]:
        summary_row = (
            await self.session.execute(
                text(
                    "SELECT running_summary, message_count "
                    "FROM conversation_summaries WHERE conversation_id = :id"
                ),
                {"id": conversation_id},
            )
        ).first()

        recent_rows = (
            await self.session.execute(
                text(
                    "SELECT role, content FROM recent_messages "
                    "WHERE conversation_id = :id "
                    "ORDER BY created_at DESC LIMIT :n"
                ),
                {"id": conversation_id, "n": self.cfg.recent_window},
            )
        ).all()
        recent = [{"role": r.role, "content": r.content} for r in reversed(recent_rows)]

        return {
            "summary": (summary_row.running_summary if summary_row else "") or "",
            "recent_messages": recent,
            "message_count": (summary_row.message_count if summary_row else 0),
        }

    def build_prompt_messages(
        self,
        *,
        system_prompt: str,
        context: dict[str, Any],
        new_user_message: str,
    ) -> list[dict[str, str]]:
        """Build messages array per architecture: [system, summary, last_N, new]."""
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        summary = context.get("summary") or ""
        if summary.strip():
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Conversation summary so far (use as primary memory; "
                        "the verbatim transcript is NOT available):\n\n" + summary
                    ),
                }
            )
        messages.extend(context.get("recent_messages", []))
        messages.append({"role": "user", "content": new_user_message})
        return messages

    # ------------------------------------------------------------------
    # Persist + summarize
    # ------------------------------------------------------------------
    async def append_message(self, conversation_id: str, role: str, content: str) -> None:
        await self.session.execute(
            text(
                "INSERT INTO recent_messages (conversation_id, role, content) "
                "VALUES (:id, :role, :content)"
            ),
            {"id": conversation_id, "role": role, "content": content},
        )
        await self.session.execute(
            text(
                portable_sql(
                    "UPDATE conversation_summaries SET message_count = message_count + 1, "
                    "updated_at = now() WHERE conversation_id = :id"
                )
            ),
            {"id": conversation_id},
        )
        await self.session.commit()

        # Trim hard cap
        await self.session.execute(
            text(
                """
                DELETE FROM recent_messages
                WHERE conversation_id = :id
                  AND id NOT IN (
                    SELECT id FROM recent_messages
                    WHERE conversation_id = :id
                    ORDER BY created_at DESC
                    LIMIT :keep
                  )
                """
            ),
            {"id": conversation_id, "keep": self.cfg.trim_after},
        )
        await self.session.commit()

        await self._maybe_summarize(conversation_id)

    async def _maybe_summarize(self, conversation_id: str) -> None:
        row = (
            await self.session.execute(
                text(
                    "SELECT running_summary, message_count "
                    "FROM conversation_summaries WHERE conversation_id = :id"
                ),
                {"id": conversation_id},
            )
        ).first()
        if not row or row.message_count == 0:
            return
        if row.message_count % self.cfg.summarize_every != 0:
            return

        recent_rows = (
            await self.session.execute(
                text(
                    "SELECT role, content FROM recent_messages "
                    "WHERE conversation_id = :id "
                    "ORDER BY created_at DESC LIMIT :n"
                ),
                {"id": conversation_id, "n": self.cfg.summarize_every},
            )
        ).all()
        new_block = "\n\n".join(f"[{r.role}] {r.content}" for r in reversed(recent_rows))

        prompt = SUMMARIZE_PROMPT.format(
            prior_summary=row.running_summary or "(none yet)",
            new_messages=new_block,
        )
        resp = await self.llm.chat([{"role": "user", "content": prompt}])
        await self.session.execute(
            text(
                portable_sql(
                    "UPDATE conversation_summaries "
                    "SET running_summary = :s, last_summarized_at = now(), updated_at = now() "
                    "WHERE conversation_id = :id"
                )
            ),
            {"s": resp.content.strip(), "id": conversation_id},
        )
        await self.session.commit()
