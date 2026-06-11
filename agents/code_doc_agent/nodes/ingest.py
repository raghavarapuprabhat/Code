"""Phase 1 — walk the project, classify files, persist inventory."""
from __future__ import annotations

import structlog
from sqlalchemy import text

from shared.storage import get_session, init_db
from ..tools.fs_tools import project_id_for, walk_project
from ..state import CodeDocState

logger = structlog.get_logger()


async def ingest_node(state: CodeDocState, *, config: dict) -> dict:
    project_path = state["project_path"]
    cfg = config["code_doc"]
    inventory = walk_project(
        project_path,
        languages=cfg["languages"],
        ignore_patterns=cfg["ignore_patterns"],
    )
    pid = project_id_for(project_path)
    logger.info(
        "ingest_done",
        project_id=pid,
        files=len(inventory),
        path=project_path,
    )

    # Ensure the schema exists (SQLite default has no seed/container to create it).
    await init_db()
    async with get_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO code_projects (id, project_path, display_name)
                VALUES (:id, :path, :name)
                ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name
                """
            ),
            {
                "id": pid,
                "path": project_path,
                "name": state.get("display_name") or project_path.split("/")[-1],
            },
        )
        await session.commit()

    return {
        "project_id": pid,
        "file_inventory": inventory,
        "verify_loops": 0,
    }
