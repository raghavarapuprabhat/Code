"""Phase 6d — persist the Architecture Model to `architecture_models` (§8.8.1).

Writes the synthesized model JSON + a stable model_hash. Reads the PRIOR model_hash
first and stashes it in state (`prev_model_hash`) so the drift-digest node (§8.9.4) can
diff old vs new. Runs before doc generation so the docs can render `model_hash` into
their staleness contract (§8.8.5).
"""
from __future__ import annotations

import hashlib
import json

import structlog
from sqlalchemy import text

from shared.storage import get_session, init_db, is_sqlite, portable_sql
from ..state import CodeDocState

logger = structlog.get_logger()

_PG_DDL = """
CREATE TABLE IF NOT EXISTS architecture_models (
    project_id   TEXT PRIMARY KEY REFERENCES code_projects(id) ON DELETE CASCADE,
    model_json   TEXT NOT NULL,
    model_hash   TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_UPSERT = """
INSERT INTO architecture_models (project_id, model_json, model_hash, generated_at)
VALUES (:pid, :model_json, :model_hash, now())
ON CONFLICT (project_id) DO UPDATE SET
    model_json = EXCLUDED.model_json,
    model_hash = EXCLUDED.model_hash,
    generated_at = now()
"""


async def arch_persist_node(state: CodeDocState, *, config: dict) -> dict:
    model = state.get("architecture_model") or {}
    pid = state["project_id"]
    if not model:
        logger.info("arch_persist_skipped", reason="no model")
        return {"model_hash": "", "prev_model_hash": None}

    model_json = json.dumps(model, sort_keys=True, default=str)
    model_hash = hashlib.sha256(model_json.encode("utf-8")).hexdigest()

    if is_sqlite():
        await init_db()

    prev_hash: str | None = None
    async with get_session() as session:
        if not is_sqlite():
            await session.execute(text(_PG_DDL))
        # Read prior hash for the drift digest before overwriting.
        row = (
            await session.execute(
                text("SELECT model_hash FROM architecture_models WHERE project_id = :p"),
                {"p": pid},
            )
        ).first()
        prev_hash = row.model_hash if row else None

        await session.execute(
            text(portable_sql(_UPSERT)),
            {"pid": pid, "model_json": model_json, "model_hash": model_hash},
        )
        await session.commit()

    logger.info("arch_persist_done", project_id=pid, model_hash=model_hash[:12],
                changed=(prev_hash != model_hash))
    return {"model_hash": model_hash, "prev_model_hash": prev_hash}
