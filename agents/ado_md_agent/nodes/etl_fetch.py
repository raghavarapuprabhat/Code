"""ETL phase 1+2 — load squads from config and fetch their workitems via MCP."""
from __future__ import annotations

import asyncio
from datetime import date

import structlog

from ..state import ETLState
from ..tools.ado_fetch import fetch_workitems_for_squad

logger = structlog.get_logger()


async def list_squads_node(state: ETLState, *, config: dict) -> dict:
    squads = config["ado"].get("squads") or []
    snap = state.get("snapshot_date") or date.today().isoformat()
    return {"squads": squads, "snapshot_date": snap, "errors": []}


async def fetch_workitems_node(state: ETLState, *, config: dict) -> dict:
    squads = state.get("squads") or []
    iteration_token = config["ado"].get("current_iteration_token")
    sem = asyncio.Semaphore(4)
    workitems_by_squad: dict[str, list[dict]] = {}
    errors: list[dict] = list(state.get("errors") or [])

    async def fetch_one(squad: dict) -> None:
        async with sem:
            try:
                items = await fetch_workitems_for_squad(
                    areapath=squad["areapath"],
                    iteration=iteration_token,
                )
                workitems_by_squad[squad["name"]] = items
            except Exception as e:  # noqa: BLE001
                logger.exception("fetch_failed", squad=squad["name"])
                errors.append({"step": "fetch", "squad": squad["name"], "err": str(e)})
                workitems_by_squad[squad["name"]] = []

    await asyncio.gather(*(fetch_one(s) for s in squads))
    logger.info("etl_fetch_done", squads=len(squads), errors=len(errors))
    return {"workitems_by_squad": workitems_by_squad, "errors": errors}
