"""Phase 4 — diff current file hashes against last-stored hashes."""
from __future__ import annotations

import structlog
from sqlalchemy import text

from shared.storage import get_session
from ..state import CodeDocState

logger = structlog.get_logger()


async def incremental_check_node(state: CodeDocState, *, config: dict) -> dict:
    pid = state["project_id"]
    inventory = state["file_inventory"]
    mode = state.get("mode", "full")

    if mode == "full":
        dirty = [f["relative_path"] for f in inventory]
        logger.info("full_mode", files=len(dirty))
        return {"dirty_files": dirty}

    async with get_session() as session:
        rows = (
            await session.execute(
                text("SELECT relative_path, last_hash FROM code_files WHERE project_id = :id"),
                {"id": pid},
            )
        ).all()
    last_hash = {r.relative_path: r.last_hash for r in rows}

    dirty: list[str] = []
    for f in inventory:
        prev = last_hash.get(f["relative_path"])
        if prev != f["sha256"]:
            dirty.append(f["relative_path"])
    logger.info("incremental_diff", changed=len(dirty), total=len(inventory))
    return {"dirty_files": dirty}
