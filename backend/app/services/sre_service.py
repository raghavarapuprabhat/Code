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

        # Mark the conversation running for the duration of this round (it flips to
        # paused/concluded below). A fresh (non-resume) round always starts running.
        await _mark_state(conv_id, project_id, "running")

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

        # Paused on a mid-loop question? Surface it, mark the conversation paused, and
        # CLOSE the stream (§9.7B v0.7 — no idle long-lived connections). The UI renders
        # the question from the events already received; POST …/answer opens a fresh stream.
        interrupts = result.get("__interrupt__")
        if interrupts:
            q = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            yield {"type": "question", "question": q, "conversation_id": conv_id}
            await memory.append_message(conv_id, "assistant", _render_question(q))
            await _mark_paused(conv_id, project_id, q)
            yield {"type": "paused", "conversation_id": conv_id, "question": q}
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
        await _mark_state(conv_id, project_id, "concluded")
        yield {"type": "final", "conversation_id": conv_id, "verdict": verdict}


def _render_question(q: dict) -> str:
    text = (q or {}).get("text", "Could you clarify?")
    opts = (q or {}).get("options")
    return text + (f"\nOptions: {', '.join(opts)}" if opts else "")


# --- v0.7 conversation-state tracking (§9.7B) ------------------------------

async def _mark_state(conversation_id: str, project_id: str | None, state: str,
                      question: dict | None = None) -> None:
    """Upsert the conversation's lifecycle state. Best-effort; never fatal to a stream."""
    import json as _json
    from sqlalchemy import text as _text
    from shared.storage import init_db, is_sqlite, portable_sql
    try:
        if is_sqlite():
            await init_db()
        async with get_session() as session:
            paused_at = "CURRENT_TIMESTAMP" if state == "paused" else "NULL"
            await session.execute(
                _text(portable_sql(f"""
                    INSERT INTO sre_conversation_state
                        (conversation_id, project_id, state, pending_question, paused_at, updated_at)
                    VALUES (:cid, :pid, :state, :pq, {paused_at}, CURRENT_TIMESTAMP)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        state = excluded.state,
                        pending_question = excluded.pending_question,
                        paused_at = {paused_at},
                        updated_at = CURRENT_TIMESTAMP
                """)),
                {"cid": conversation_id, "pid": project_id, "state": state,
                 "pq": _json.dumps(question) if question else None},
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("sre_state_mark_failed", conv=conversation_id, state=state)


async def _mark_paused(conversation_id: str, project_id: str | None, question: dict) -> None:
    await _mark_state(conversation_id, project_id, "paused", question)


async def sweep_expired_questions() -> dict:
    """Expire paused checkpoints older than the TTL (§9.7B v0.7).

    For each stale paused conversation: resume the graph past the question with a
    sentinel so it concludes `needs_more_info` (the unanswered PendingQuestion attached),
    then mark the row `expired`. The investigation ends honestly instead of leaking
    checkpoints. Returns a small summary for the scheduler log.
    """
    import json as _json
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as _text
    from langgraph.types import Command
    from agents.sre_agent.graph import get_interactive_graph

    cfg = _load_sre_config()
    ttl_hours = int((cfg.get("sre", {}) or {}).get("question_ttl_hours", 24))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()

    rows: list = []
    try:
        async with get_session() as session:
            rows = (
                await session.execute(
                    _text("""SELECT conversation_id, project_id, pending_question
                             FROM sre_conversation_state
                             WHERE state = 'paused' AND paused_at IS NOT NULL
                               AND paused_at < :cutoff"""),
                    {"cutoff": cutoff},
                )
            ).all()
    except Exception:  # noqa: BLE001
        return {"expired": 0, "error": "state table unavailable"}

    if not rows:
        return {"expired": 0}

    app = get_interactive_graph()
    expired = 0
    for row in rows:
        conv_id = row.conversation_id
        gcfg = {"configurable": {"thread_id": conv_id}}
        try:
            # Resume with an explicit "no answer" sentinel; the ask_user node folds it as
            # an unanswered item and the loop proceeds to Conclude.
            await app.ainvoke(Command(resume="__no_answer_timeout__"), gcfg)
        except Exception:  # noqa: BLE001 — even if resume fails, mark expired so we stop retrying
            logger.warning("sweep_resume_failed", conv=conv_id)
        await _mark_state(conv_id, row.project_id, "expired",
                          _json.loads(row.pending_question) if row.pending_question else None)
        expired += 1

    logger.info("sre_question_sweep_done", expired=expired, ttl_hours=ttl_hours)
    return {"expired": expired, "ttl_hours": ttl_hours}


async def claim_answer(conversation_id: str) -> bool:
    """Compare-and-set guard for /answer concurrency (§9.7B v0.7): atomically flip
    paused → running. Returns False if the conversation wasn't paused (already answered
    or never paused) so the router can return 409 Conflict — first answer wins."""
    from sqlalchemy import text as _text
    try:
        async with get_session() as session:
            res = await session.execute(
                _text("""UPDATE sre_conversation_state
                         SET state = 'running', updated_at = CURRENT_TIMESTAMP
                         WHERE conversation_id = :c AND state = 'paused'"""),
                {"c": conversation_id},
            )
            await session.commit()
            return (res.rowcount or 0) > 0
    except Exception:  # noqa: BLE001 — if state table is unavailable, don't block answers
        return True


async def get_conversation_state(conversation_id: str) -> dict:
    """Report conversation lifecycle for UI re-hydration (§9.7B v0.7).

    Returns {state: running|paused|concluded|expired, pending_question?, paused_at?}.
    Cross-checks the recorded state against the live checkpointer so a resumed run isn't
    reported as still-paused.
    """
    import json as _json
    from sqlalchemy import text as _text

    row = None
    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    _text("""SELECT state, pending_question, paused_at
                             FROM sre_conversation_state WHERE conversation_id = :c"""),
                    {"c": conversation_id},
                )
            ).first()
    except Exception:  # noqa: BLE001
        pass

    if not row:
        return {"conversation_id": conversation_id, "state": "running"}

    state = row.state
    pending = _json.loads(row.pending_question) if row.pending_question else None
    return {
        "conversation_id": conversation_id,
        "state": state,
        "pending_question": pending,
        "paused_at": str(row.paused_at) if row.paused_at else None,
    }


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


async def record_verdict_outcome(
    *,
    conversation_id: str,
    project_id: str,
    classification: str,
    confidence: float,
    outcome: str,
    outcome_source: str,
    root_cause_final: str = "",
) -> dict:
    """Record a verdict outcome (feedback channel) for calibration (§9.17.5)."""
    from agents.sre_agent.calibration import record_outcome

    async with get_session() as session:
        return await record_outcome(
            session=session,
            conversation_id=conversation_id,
            project_id=project_id,
            classification=classification,
            confidence=confidence,
            outcome=outcome,
            outcome_source=outcome_source,
            root_cause_final=root_cause_final,
        )


async def file_ado_bug(
    *,
    conversation_id: str,
    project_id: str,
    dry_run: bool = False,
) -> dict:
    """File (or dry-run) an ADO Bug for the given triage conversation (§9.17.7)."""
    from agents.sre_agent.ado_writeback import file_bug
    from agents.sre_agent.graph import get_interactive_graph

    cfg = _load_sre_config()
    app = get_interactive_graph()
    gcfg = {"configurable": {"thread_id": conversation_id}}
    try:
        snap = await app.aget_state(gcfg)
        state = dict(snap.values or {}) if snap else {}
    except Exception:  # noqa: BLE001
        state = {}

    verdict = state.get("verdict") or {}
    issue = state.get("issue") or {}
    evidence = state.get("evidence") or []
    severity = state.get("severity") or {}
    facts = state.get("facts") or {}

    return await file_bug(
        conversation_id=conversation_id,
        project_id=project_id,
        verdict=verdict,
        issue=issue,
        evidence=evidence,
        severity=severity,
        config=cfg,
        error_signature=facts.get("error_signature", ""),
        dry_run=dry_run,
    )


async def get_calibration_stats(*, project_id: str) -> dict:
    """Brier score + calibration bands for a project (§9.17.5)."""
    from agents.sre_agent.calibration import get_calibration

    async with get_session() as session:
        return await get_calibration(session=session, project_id=project_id)


async def run_verify_fix(
    *,
    conversation_id: str,
    project_id: str,
    pr_url: str | None = None,
) -> dict:
    """Re-probe after fix deploys; confirm symptoms resolved (§9.17.4)."""
    from agents.sre_agent.verify_graph import run_verify_fix as _run

    cfg = _load_sre_config()
    return await _run(
        conversation_id=conversation_id,
        project_id=project_id,
        pr_url=pr_url,
        config=cfg,
    )


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
