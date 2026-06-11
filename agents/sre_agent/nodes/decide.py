"""Phase 4 — terminal nodes: handoff to fixer, close not-a-bug, or ask follow-up."""
from __future__ import annotations

import structlog

from ..state import SREState

logger = structlog.get_logger()


async def handoff_fixer_node(state: SREState, *, config: dict) -> dict:
    """Emit a bug packet rich enough that the Fixer starts at PlanFix, not re-investigation (§9.12)."""
    verdict = state.get("verdict") or {}
    issue = state.get("issue") or {}
    facts = state.get("facts") or {}
    evidence = state.get("evidence") or []

    suspect = list(verdict.get("likely_files", []))
    regression_commit = _regression_commit(evidence)
    conv_id = state.get("conversation_id")

    payload = {
        "project_id": state.get("project_id"),
        "issue": issue,
        "root_cause": verdict.get("root_cause", ""),
        "suspect_locations": suspect,
        "regression_commit": regression_commit,
        "evidence": evidence,
        "citations": verdict.get("citations", []),
        "suggested_fix_area": verdict.get("next_step", ""),
        "repro": issue.get("repro_steps", ""),
        "confidence": verdict.get("confidence"),
        "conversation_link": f"/conversations/{conv_id}" if conv_id else None,
        # Retained for backward compatibility with the shipped Fixer ContextLoad.
        "verdict": verdict,
        "likely_files": suspect,
        "rag_hits": state.get("rag_hits", []),
    }
    logger.info(
        "sre_handoff_fixer",
        confidence=verdict.get("confidence"),
        files=len(suspect),
        regression=regression_commit,
    )
    return {"handoff": payload}


def _regression_commit(evidence: list[dict]) -> str | None:
    """Pull a commit ref out of any git-sourced evidence citation."""
    import re

    for e in evidence:
        if e.get("source") == "git":
            m = re.search(r"\b([0-9a-f]{7,40})\b", e.get("citation", "") + " " + e.get("finding", ""))
            if m:
                return m.group(1)
    return None


async def close_not_bug_node(state: SREState, *, config: dict) -> dict:
    verdict = state.get("verdict") or {}
    logger.info("sre_close_not_bug", confidence=verdict.get("confidence"))
    return {"handoff": None}


async def ask_followup_node(state: SREState, *, config: dict) -> dict:
    """Bump the followup counter; the actual question text is in verdict.questions."""
    rounds = int(state.get("followup_round", 0)) + 1
    logger.info("sre_ask_followup", round=rounds)
    return {"followup_round": rounds}
