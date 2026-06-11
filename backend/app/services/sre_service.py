"""Bridge from the FastAPI backend to the standalone SRE Agent."""
from __future__ import annotations

import io
import csv
import json
import os
import sys
from typing import AsyncIterator

import structlog
from sqlalchemy import text

from shared.memory import MemoryConfig, MemoryManager
from shared.llm_adapter import build_adapter_from_config
from shared.storage import get_session, portable_sql

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = structlog.get_logger()


def _load_sre_config() -> dict:
    import yaml
    cfg_path = os.path.normpath(os.path.join(_REPO_ROOT, "agents/sre_agent/config.yaml"))
    with open(cfg_path) as fh:
        return yaml.safe_load(fh)


async def stream_triage(
    *,
    project_id: str,
    user_message: str,
    conversation_id: str | None,
    user_id: str | None,
) -> AsyncIterator[dict]:
    """Run one triage round and stream structured SSE events.

    Multi-turn: re-issue this with the same conversation_id and the user's
    follow-up answer; we replay prior_state from the conversation_summaries row.
    """
    from agents.sre_agent.graph import run_triage  # local import — heavy deps

    cfg = _load_sre_config()
    llm = build_adapter_from_config(cfg)
    mem_cfg = MemoryConfig.from_dict(cfg.get("memory", {}))

    async with get_session() as session:
        memory = MemoryManager(session, llm, mem_cfg)
        conv_id = await memory.get_or_create_conversation(
            agent_name="sre",
            scope_key=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        yield {"type": "start", "conversation_id": conv_id}

        # Reload prior triage state if any (stored as JSON in the summary row).
        prior_state = await _load_prior_state(session, conv_id)

        await memory.append_message(conv_id, "user", user_message)
        yield {"type": "node", "name": "understand"}

        try:
            result = await run_triage(
                project_id=project_id,
                user_message=user_message,
                prior_state=prior_state,
                conversation_id=conv_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("sre_triage_failed", conv=conv_id)
            yield {"type": "error", "message": str(e)}
            return

        verdict = result.get("verdict") or {}
        handoff = result.get("handoff")
        rag_hits = result.get("rag_hits") or []

        yield {"type": "rag", "hits": [
            {"path": h.get("relative_path"), "score": h.get("score"),
             "collection": h.get("collection")} for h in rag_hits
        ]}
        # Replay the investigation so the UI can show the agent's reasoning (§9.13).
        # (Live step-by-step streaming via graph.astream is a later enhancement; the
        # foundation surfaces the full trace once the round completes.)
        for h in result.get("hypotheses") or []:
            yield {"type": "hypothesis", "hypothesis": h}
        for step in result.get("investigation_log") or []:
            yield {"type": "step", "step": step}
        for ev in result.get("evidence") or []:
            yield {"type": "evidence", "evidence": ev}
        yield {"type": "verdict", "verdict": verdict}
        if handoff:
            yield {"type": "handoff", "target": "sre_fixer", "payload": handoff}

        # Persist the triage state on the conversation row so multi-turn works.
        await _save_state(session, conv_id, result)

        await memory.append_message(
            conv_id,
            "assistant",
            _render_assistant_text(verdict, handoff is not None),
        )
        yield {"type": "final", "conversation_id": conv_id, "verdict": verdict}


async def triage_csv_text(*, project_id: str, csv_bytes: bytes) -> dict:
    from agents.sre_agent.graph import triage_csv  # local import

    text_data = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text_data))
    rows = list(reader)
    out_rows = await triage_csv(project_id=project_id, rows=rows)
    return {"count": len(out_rows), "rows": out_rows}


def _render_assistant_text(verdict: dict, handed_off: bool) -> str:
    cls = verdict.get("classification", "needs_more_info")
    conf = verdict.get("confidence", 0)
    rationale = verdict.get("rationale", "")
    root_cause = verdict.get("root_cause", "")
    citations = verdict.get("citations", []) or []
    qs = verdict.get("questions", []) or []
    parts = [f"Verdict: **{cls}** (confidence {conf:.0%})", ""]
    if root_cause:
        parts += [f"**Root cause:** {root_cause}", ""]
    parts.append(rationale)
    if citations:
        parts += ["", "Evidence: " + ", ".join(f"`{c}`" for c in citations[:8])]
    if qs:
        parts.append("")
        parts.append("Follow-up questions:")
        for q in qs:
            parts.append(f"- {q}")
    if handed_off:
        parts.append("")
        parts.append("Handing this off to the SRE Fixer Agent.")
    return "\n".join(parts)


async def _load_prior_state(session, conversation_id: str) -> dict | None:
    row = (
        await session.execute(
            text(
                "SELECT preferences FROM user_preferences "
                "WHERE user_id = :u AND agent_name = 'sre_state'"
            ),
            {"u": conversation_id},
        )
    ).first()
    if row and row.preferences:
        try:
            return dict(row.preferences)
        except Exception:
            return None
    return None


async def _save_state(session, conversation_id: str, state: dict) -> None:
    # We piggyback on user_preferences (keyed by conversation_id) for simplicity.
    keep = {
        "project_id": state.get("project_id"),
        "issue": state.get("issue"),
        "facts": state.get("facts") or {},
        "hypotheses": state.get("hypotheses") or [],
        "evidence": state.get("evidence") or [],
        "investigation_log": state.get("investigation_log") or [],
        "budget": state.get("budget") or {},
        "classification_history": state.get("classification_history") or [],
        "followup_round": state.get("followup_round", 0),
        "rag_hits": state.get("rag_hits") or [],
    }
    await session.execute(
        text(
            portable_sql(
                """
            INSERT INTO user_preferences (user_id, agent_name, preferences)
            VALUES (:u, 'sre_state', CAST(:p AS JSONB))
            ON CONFLICT (user_id, agent_name)
            DO UPDATE SET preferences = EXCLUDED.preferences, updated_at = now()
            """
            )
        ),
        {"u": conversation_id, "p": json.dumps(keep)},
    )
    await session.commit()
