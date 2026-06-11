"""Bridge from the FastAPI backend to the standalone SRE Agent."""
from __future__ import annotations

import io
import csv
import os
import sys
from typing import AsyncIterator

import structlog

from shared.memory import MemoryConfig, MemoryManager
from shared.llm_adapter import build_adapter_from_config
from shared.storage import get_session

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

    Uses the interactive (checkpointed) graph so a mid-loop ask_user can pause and
    resume per conversation (thread_id). If the incoming message answers a pending
    question, it resumes the frozen investigation instead of starting a new round.
    """
    from agents.sre_agent.graph import get_interactive_graph
    from langgraph.types import Command

    cfg = _load_sre_config()
    llm = build_adapter_from_config(cfg)
    mem_cfg = MemoryConfig.from_dict(cfg.get("memory", {}))
    app = get_interactive_graph()

    async with get_session() as session:
        memory = MemoryManager(session, llm, mem_cfg)
        conv_id = await memory.get_or_create_conversation(
            agent_name="sre",
            scope_key=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        yield {"type": "start", "conversation_id": conv_id}
        await memory.append_message(conv_id, "user", user_message)

        gcfg = {"configurable": {"thread_id": conv_id}}
        try:
            snap = await app.aget_state(gcfg)
            resuming = bool(snap and "ask_user" in (snap.next or ()))
        except Exception:  # noqa: BLE001 — fresh thread
            resuming = False

        yield {"type": "node", "name": "ask_user" if resuming else "understand"}
        try:
            if resuming:
                result = await app.ainvoke(Command(resume=user_message), gcfg)
            else:
                initial = {
                    "project_id": project_id,
                    "user_message": user_message,
                    "conversation_id": conv_id,
                    "allow_interrupt": True,
                }
                result = await app.ainvoke(initial, gcfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("sre_triage_failed", conv=conv_id)
            yield {"type": "error", "message": str(e)}
            return

        # Paused on a mid-loop question? Surface it and end the round (state is frozen).
        interrupts = result.get("__interrupt__")
        if interrupts:
            q = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            yield {"type": "question", "question": q, "conversation_id": conv_id}
            await memory.append_message(conv_id, "assistant", _render_question(q))
            yield {"type": "final", "conversation_id": conv_id, "awaiting": "answer"}
            return

        verdict = result.get("verdict") or {}
        handoff = result.get("handoff")
        rag_hits = result.get("rag_hits") or []

        yield {"type": "rag", "hits": [
            {"path": h.get("relative_path"), "score": h.get("score"),
             "collection": h.get("collection")} for h in rag_hits
        ]}
        for h in result.get("hypotheses") or []:
            yield {"type": "hypothesis", "hypothesis": h}
        for step in result.get("investigation_log") or []:
            yield {"type": "step", "step": step}
        for ev in result.get("evidence") or []:
            yield {"type": "evidence", "evidence": ev}
        for p in result.get("probe_log") or []:
            yield {"type": "probe", "probe": p}
        if result.get("severity"):
            yield {"type": "severity", "severity": result["severity"]}
        yield {"type": "verdict", "verdict": verdict}
        if handoff:
            yield {"type": "handoff", "target": "sre_fixer", "payload": handoff}

        await memory.append_message(
            conv_id,
            "assistant",
            _render_assistant_text(verdict, handoff is not None),
        )
        yield {"type": "final", "conversation_id": conv_id, "verdict": verdict}


def _render_question(q: dict) -> str:
    text = (q or {}).get("text", "Could you clarify?")
    opts = (q or {}).get("options")
    return text + (f"\nOptions: {', '.join(opts)}" if opts else "")


async def steer_triage(*, conversation_id: str, action: str, hypothesis_id: str | None,
                       statement: str | None) -> dict:
    """Pin / inject / kill a hypothesis on a live (paused) investigation (§9.17.8).

    The steering action is written into the checkpointed state; it is applied at the
    next Plan step (on resume / next message). Returns the action recorded.
    """
    from agents.sre_agent.graph import get_interactive_graph

    app = get_interactive_graph()
    gcfg = {"configurable": {"thread_id": conversation_id}}
    snap = await app.aget_state(gcfg)
    existing = list((snap.values or {}).get("steering") or []) if snap else []
    existing.append({"action": action, "id": hypothesis_id, "statement": statement})
    await app.aupdate_state(gcfg, {"steering": existing})
    return {"ok": True, "queued": {"action": action, "id": hypothesis_id, "statement": statement}}


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

# Interactive multi-turn state now lives in the LangGraph checkpointer (keyed by
# conversation_id as thread_id), so the prior user_preferences state hack is gone.
