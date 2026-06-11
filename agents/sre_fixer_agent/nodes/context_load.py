"""Phase 1 — resolve repo path, validate workspace, set initial state."""
from __future__ import annotations

import os

import structlog
from sqlalchemy import text

from shared.storage import get_session
from ..state import FixerState

logger = structlog.get_logger()


async def context_load_node(state: FixerState, *, config: dict) -> dict:
    project_id = state.get("project_id")
    repo_path = state.get("repo_path")

    if not repo_path and project_id:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT project_path FROM code_projects WHERE id = :id"),
                    {"id": project_id},
                )
            ).first()
        if row:
            repo_path = row.project_path

    if not repo_path or not os.path.isdir(repo_path):
        return {
            "status": "error",
            "error": f"Repo path not found for project_id={project_id!r}",
            "audit_trail": (state.get("audit_trail") or []) + [
                {"step": "context_load", "status": "error", "detail": "no repo path"}
            ],
        }

    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return {
            "status": "error",
            "error": f"Not a git repository: {repo_path}",
            "audit_trail": (state.get("audit_trail") or []) + [
                {"step": "context_load", "status": "error", "detail": "not a git repo"}
            ],
        }

    logger.info("fixer_context_loaded", project_id=project_id, repo=repo_path)
    return {
        "repo_path": repo_path,
        "attempt": int(state.get("attempt", 0)) + 1,
        "status": "planning",
        "audit_trail": (state.get("audit_trail") or []) + [
            {"step": "context_load", "status": "ok", "repo_path": repo_path}
        ],
    }
