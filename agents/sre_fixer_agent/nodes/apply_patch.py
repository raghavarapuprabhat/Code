"""Phase 3 — write the planned edits to disk."""
from __future__ import annotations

import structlog

from ..state import FixerState
from ..tools.patch_tools import PatchSafetyError, apply_edits

logger = structlog.get_logger()


async def apply_patch_node(state: FixerState, *, config: dict) -> dict:
    plan = state.get("plan") or {}
    repo_path = state["repo_path"]
    edits = plan.get("edits") or []
    try:
        touched = apply_edits(repo_path, edits)
    except PatchSafetyError as e:
        logger.error("patch_safety_blocked", err=str(e))
        return {
            "status": "raised_human",
            "error": f"Patch blocked by safety policy: {e}",
            "audit_trail": (state.get("audit_trail") or []) + [
                {"step": "apply_patch", "status": "blocked", "detail": str(e)}
            ],
        }
    logger.info("fixer_patch_applied", files=len(touched))
    return {
        "status": "applied",
        "audit_trail": (state.get("audit_trail") or []) + [
            {"step": "apply_patch", "status": "ok", "files": touched}
        ],
    }
