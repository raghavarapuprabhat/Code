"""ETL phase 4 — derive RAID items from workitem types/tags."""
from __future__ import annotations

import structlog

from ..state import ETLState
from .etl_metrics import _parse_date

logger = structlog.get_logger()

TYPE_MAP = {
    "risk": "Risk",
    "issue": "Issue",
    "dependency": "Dependency",
    "assumption": "Assumption",
    "bug": "Issue",          # Bugs in flight surface as Issues
    "blocker": "Issue",
}


def _classify(item: dict, raid_tags: list[str]) -> str | None:
    wit = (item.get("work_item_type") or "").lower()
    if wit in TYPE_MAP:
        return TYPE_MAP[wit]
    for tag in (item.get("tags") or []):
        t = tag.lower()
        if t in TYPE_MAP:
            return TYPE_MAP[t]
        if t in [r.lower() for r in raid_tags]:
            return TYPE_MAP.get(t, t.capitalize())
    return None


def _severity(item: dict) -> str | None:
    for tag in (item.get("tags") or []):
        t = tag.lower()
        if t in {"sev-1", "sev1", "high", "critical", "p0", "p1"}:
            return "High"
        if t in {"sev-2", "sev2", "medium", "p2"}:
            return "Medium"
        if t in {"sev-3", "sev3", "low", "p3"}:
            return "Low"
    return None


async def detect_raid_node(state: ETLState, *, config: dict) -> dict:
    raid_tags = config["ado"].get("raid_tags", [])
    raids: list[dict] = []
    snap = state["snapshot_date"]
    DONE = {"Done", "Closed", "Resolved", "Completed"}

    for squad_name, items in (state.get("workitems_by_squad") or {}).items():
        for it in items:
            if it.get("state") in DONE:
                continue
            kind = _classify(it, raid_tags)
            if not kind:
                continue
            raids.append(
                {
                    "snapshot_date": snap,
                    "squad_name": squad_name,
                    "type": kind,
                    "title": (it.get("title") or "").strip()[:300],
                    "severity": _severity(it),
                    "owner": it.get("assigned_to"),
                    "due_date": _parse_date(it.get("target_date")),
                    "workitem_id": it.get("id"),
                }
            )
    logger.info("etl_raid_done", count=len(raids))
    return {"raids": raids}
