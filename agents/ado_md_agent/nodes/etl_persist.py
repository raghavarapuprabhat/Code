"""ETL phase 6 — write everything to the snapshot tables."""
from __future__ import annotations

import structlog

from ..state import ETLState
from ..tools.snapshot_db import (
    replace_achievements_for_date,
    replace_raid_for_date,
    upsert_squad_snapshot,
)

logger = structlog.get_logger()


async def persist_snapshots_node(state: ETLState, *, config: dict) -> dict:
    snap = state["snapshot_date"]
    metrics_n = await upsert_squad_snapshot(state.get("metrics") or [])
    raid_n = await replace_raid_for_date(snap, state.get("raids") or [])
    ach_n = await replace_achievements_for_date(snap, state.get("achievements") or [])
    persisted = {"squad_metrics": metrics_n, "raids": raid_n, "achievements": ach_n}
    logger.info("etl_persist_done", snap=snap, **persisted)
    return {"persisted": persisted}
