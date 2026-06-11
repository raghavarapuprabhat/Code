"""ETL phase 3 — compute per-squad metrics deterministically (no LLM)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog

from ..state import ETLState

logger = structlog.get_logger()

DONE_STATES = {"Done", "Closed", "Resolved", "Completed"}
IN_PROGRESS_STATES = {"Active", "Doing", "In Progress", "Committed"}
NEW_STATES = {"New", "To Do", "Proposed"}


def _parse_date(val: Any) -> date | None:
    if not val:
        return None
    if isinstance(val, date):
        return val
    s = str(val).split("T")[0]
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _is_blocked(item: dict, blocked_tag: str) -> bool:
    tags = [t.lower() for t in (item.get("tags") or [])]
    return blocked_tag.lower() in tags


def _is_overdue(item: dict, today: date) -> bool:
    if item.get("state") in DONE_STATES:
        return False
    td = _parse_date(item.get("target_date"))
    return bool(td and td < today)


def _utilization(in_progress: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((in_progress / total) * 100.0, 1)


def _velocity_3sprint_avg(items: list[dict]) -> float:
    """Approximate 3-sprint velocity from points completed in distinct iterations."""
    by_iter: dict[str, float] = {}
    for it in items:
        if it.get("state") not in DONE_STATES:
            continue
        sp = it.get("story_points")
        if sp is None:
            continue
        ip = it.get("iteration_path") or "(none)"
        by_iter[ip] = by_iter.get(ip, 0.0) + float(sp)
    if not by_iter:
        return 0.0
    last3 = sorted(by_iter.items(), key=lambda kv: kv[0])[-3:]
    return round(sum(v for _, v in last3) / max(len(last3), 1), 1)


async def compute_metrics_node(state: ETLState, *, config: dict) -> dict:
    snap_str = state["snapshot_date"]
    today = date.fromisoformat(snap_str)
    blocked_tag = config["ado"].get("blocked_tag", "blocked")

    metrics: list[dict] = []
    for squad_name, items in (state.get("workitems_by_squad") or {}).items():
        total = len(items)
        in_progress = sum(1 for i in items if i.get("state") in IN_PROGRESS_STATES)
        done_this_sprint = sum(
            1 for i in items
            if i.get("state") in DONE_STATES and _parse_date(i.get("closed_date")) == today
        )
        # If the snapshot date is the current day we may be running mid-sprint;
        # also count anything closed in last 7 days as "this sprint" for the dashboard.
        if done_this_sprint == 0:
            cutoff = today
            done_this_sprint = sum(
                1 for i in items
                if i.get("state") in DONE_STATES
                and (_parse_date(i.get("closed_date")) or today) >= cutoff
                .replace(day=max(cutoff.day - 7, 1))
            )
        blocked = sum(1 for i in items if _is_blocked(i, blocked_tag))
        overdue = sum(1 for i in items if _is_overdue(i, today))
        metrics.append(
            {
                "snapshot_date": snap_str,
                "squad_name": squad_name,
                "total_workitems": total,
                "in_progress": in_progress,
                "done_this_sprint": done_this_sprint,
                "blocked": blocked,
                "overdue": overdue,
                "velocity_3sprint_avg": _velocity_3sprint_avg(items),
                "utilization_pct": _utilization(in_progress, total),
            }
        )
    logger.info("etl_metrics_done", squads=len(metrics))
    return {"metrics": metrics}
