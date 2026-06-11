"""Phase 4 — terminal nodes: handoff to fixer, close not-a-bug, or ask follow-up."""
from __future__ import annotations

import structlog

from ..state import SREState

logger = structlog.get_logger()


async def handoff_fixer_node(state: SREState, *, config: dict) -> dict:
    verdict = state.get("verdict") or {}
    issue = state.get("issue") or {}
    payload = {
        "issue": issue,
        "verdict": verdict,
        "likely_files": verdict.get("likely_files", []),
        "rag_hits": state.get("rag_hits", []),
        "project_id": state.get("project_id"),
    }
    logger.info(
        "sre_handoff_fixer",
        confidence=verdict.get("confidence"),
        files=len(payload["likely_files"]),
    )
    return {"handoff": payload}


async def close_not_bug_node(state: SREState, *, config: dict) -> dict:
    verdict = state.get("verdict") or {}
    logger.info("sre_close_not_bug", confidence=verdict.get("confidence"))
    return {"handoff": None}


async def ask_followup_node(state: SREState, *, config: dict) -> dict:
    """Bump the followup counter; the actual question text is in verdict.questions."""
    rounds = int(state.get("followup_round", 0)) + 1
    logger.info("sre_ask_followup", round=rounds)
    return {"followup_round": rounds}
