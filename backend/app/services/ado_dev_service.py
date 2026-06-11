"""Bridge from FastAPI to the ADO Developer Assistant.

Multi-turn state is persisted in `user_preferences.preferences` JSONB blob,
keyed by (user_id, agent_name='ado_dev_agent_state').
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, AsyncIterator

import structlog
from sqlalchemy import text

from shared.storage import get_session, portable_sql

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = structlog.get_logger()

STATE_AGENT = "ado_dev_agent_state"
PERSIST_FIELDS = (
    "step",
    "last_areapath",
    "last_iteration",
    "intent",
    "what_done_text",
    "candidate_updates",
    "needs_consent",
)


async def _load_state(user_id: str) -> dict[str, Any]:
    async with get_session() as session:
        row = (await session.execute(
            text(
                "SELECT preferences FROM user_preferences "
                "WHERE user_id = :u AND agent_name = :a"
            ),
            {"u": user_id, "a": STATE_AGENT},
        )).first()
    if row and row.preferences:
        prefs = row.preferences
        if isinstance(prefs, str):
            try:
                prefs = json.loads(prefs)
            except json.JSONDecodeError:
                prefs = {}
        return dict(prefs)
    return {}


async def _save_state(user_id: str, state: dict[str, Any]) -> None:
    keep = {k: state.get(k) for k in PERSIST_FIELDS if k in state}
    async with get_session() as session:
        await session.execute(
            text(
                portable_sql(
                    """
                INSERT INTO user_preferences (user_id, agent_name, preferences)
                VALUES (:u, :a, CAST(:p AS JSONB))
                ON CONFLICT (user_id, agent_name) DO UPDATE
                SET preferences = EXCLUDED.preferences, updated_at = now()
                """
                )
            ),
            {"u": user_id, "a": STATE_AGENT, "p": json.dumps(keep, default=str)},
        )
        await session.commit()


async def stream_dev_turn(
    *,
    user_id: str,
    user_name: str | None,
    user_message: str,
    reset: bool = False,
) -> AsyncIterator[dict]:
    from agents.ado_dev_agent.graph import run_turn

    if reset:
        await _save_state(user_id, {"step": "greet"})

    persisted = {} if reset else await _load_state(user_id)
    state: dict[str, Any] = {
        **persisted,
        "user_id": user_id,
        "user_name": user_name or user_id,
        "user_message": user_message,
    }
    if not state.get("step"):
        state["step"] = "greet"

    yield {"type": "start", "step": state["step"]}

    try:
        out = await run_turn(state=state)
    except Exception as e:  # noqa: BLE001
        logger.exception("dev_turn_failed", user=user_id)
        yield {"type": "error", "message": str(e)}
        return

    state.update(out)
    await _save_state(user_id, state)

    if out.get("status_report"):
        yield {"type": "status_report", "report": out["status_report"]}
    if out.get("candidate_updates"):
        yield {"type": "candidates", "candidates": out["candidate_updates"]}
    if out.get("applied"):
        yield {"type": "applied", "applied": out["applied"]}

    yield {
        "type": "final",
        "step": out.get("step"),
        "needs_consent": bool(out.get("needs_consent")),
        "response_text": out.get("response_text") or "",
    }
