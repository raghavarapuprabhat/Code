"""Workitem fetch + status computation for the developer assistant.

Reuses the shared ADO MCP client and the field-normalization helpers from the
MD agent's fetch helper to keep the data shapes consistent across both agents.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import structlog

from shared.mcp_client import ADOMCPClient

logger = structlog.get_logger()


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


def _person(field: Any) -> str | None:
    if not field:
        return None
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return field.get("displayName") or field.get("uniqueName")
    return None


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    f = raw.get("fields") or raw
    return {
        "id": raw.get("id") or f.get("id"),
        "title": f.get("System.Title") or raw.get("title", ""),
        "state": f.get("System.State") or raw.get("state", ""),
        "assigned_to": _person(f.get("System.AssignedTo")) or raw.get("assigned_to"),
        "area_path": f.get("System.AreaPath") or raw.get("area_path"),
        "iteration_path": f.get("System.IterationPath") or raw.get("iteration_path"),
        "tags": _tags(f.get("System.Tags") or raw.get("tags")),
        "story_points": f.get("Microsoft.VSTS.Scheduling.StoryPoints") or raw.get("story_points"),
        "target_date": f.get("Microsoft.VSTS.Scheduling.TargetDate") or raw.get("target_date"),
        "start_date": f.get("Microsoft.VSTS.Scheduling.StartDate") or raw.get("start_date"),
        "closed_date": f.get("Microsoft.VSTS.Common.ClosedDate") or raw.get("closed_date"),
        "changed_date": f.get("System.ChangedDate") or raw.get("changed_date"),
        "work_item_type": f.get("System.WorkItemType") or raw.get("work_item_type", ""),
    }


def _tags(field: Any) -> list[str]:
    if not field:
        return []
    if isinstance(field, list):
        return [str(t).strip() for t in field]
    if isinstance(field, str):
        return [t.strip() for t in field.split(";") if t.strip()]
    return []


async def list_assigned(
    *,
    areapath: str,
    assigned_to: str,
    iteration: str | None = None,
) -> list[dict[str, Any]]:
    client = ADOMCPClient()
    try:
        items = await client.list_workitems(
            areapath=areapath,
            iteration=iteration,
            assigned_to=assigned_to,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("dev_list_failed", areapath=areapath, err=str(e))
        return []
    if not isinstance(items, list):
        if isinstance(items, dict) and isinstance(items.get("value"), list):
            items = items["value"]
        else:
            return []
    return [_normalize(i) for i in items]


def compute_status(
    items: list[dict],
    *,
    today: date,
    cfg: dict,
) -> dict:
    in_progress_states = set(cfg.get("in_progress_states", []))
    done_states = set(cfg.get("done_states", []))
    new_states = set(cfg.get("new_states", []))

    week_start = today - timedelta(days=today.weekday())   # Monday
    week_end = week_start + timedelta(days=6)              # Sunday
    seven_days_ago = today - timedelta(days=7)
    three_days_ago = today - timedelta(days=3)

    overdue_items: list[dict] = []
    not_started_items: list[dict] = []
    in_progress_items: list[dict] = []
    planned_items: list[dict] = []
    action_items: list[str] = []

    in_progress = 0
    overdue = 0
    planned_this_week = 0
    done_this_week = 0

    for it in items:
        st = it.get("state", "")
        td = _parse_date(it.get("target_date"))
        sd = _parse_date(it.get("start_date"))
        cd = _parse_date(it.get("closed_date"))
        ch = _parse_date(it.get("changed_date"))

        if st in done_states:
            if cd and cd >= seven_days_ago:
                done_this_week += 1
            continue

        if st in in_progress_states:
            in_progress += 1
            in_progress_items.append(it)
            if ch and ch < three_days_ago:
                action_items.append(
                    f"#{it['id']} '{it['title']}' is Active but hasn't been updated in {(today - ch).days} days"
                )

        if td and td < today and st not in done_states:
            overdue += 1
            overdue_items.append(it)
            action_items.append(f"#{it['id']} '{it['title']}' is overdue (due {td.isoformat()})")

        if td and week_start <= td <= week_end:
            planned_this_week += 1
            planned_items.append(it)

        if st in new_states and sd and sd < today:
            not_started_items.append(it)
            action_items.append(
                f"#{it['id']} '{it['title']}' was supposed to start {sd.isoformat()} but is still {st}"
            )

    # Velocity: average story points completed across last 3 distinct iterations.
    by_iter: dict[str, float] = {}
    for it in items:
        if it.get("state") in done_states and it.get("story_points") is not None:
            ip = it.get("iteration_path") or "(none)"
            by_iter[ip] = by_iter.get(ip, 0.0) + float(it["story_points"])
    last3 = sorted(by_iter.items(), key=lambda kv: kv[0])[-3:]
    velocity = round(sum(v for _, v in last3) / max(len(last3), 1), 1) if last3 else 0.0

    # Sprint utilization: committed pts (anything assigned to this iteration) vs done pts in current iteration.
    current_iter_items = [it for it in items if it.get("iteration_path")]
    committed = sum(float(it.get("story_points") or 0) for it in current_iter_items)
    completed = sum(
        float(it.get("story_points") or 0)
        for it in current_iter_items
        if it.get("state") in done_states
    )
    util = round((completed / committed) * 100.0, 1) if committed > 0 else 0.0

    return {
        "assigned": len(items),
        "in_progress": in_progress,
        "overdue": overdue,
        "planned_this_week": planned_this_week,
        "done_this_week": done_this_week,
        "velocity_3sprint_avg": velocity,
        "sprint_utilization_pct": util,
        "overdue_items": overdue_items[:20],
        "not_started_items": not_started_items[:20],
        "in_progress_items": in_progress_items[:30],
        "planned_items": planned_items[:20],
        "action_items": action_items[:30],
    }


async def update_workitem(
    workitem_id: int,
    *,
    comment: str | None = None,
    new_state: str | None = None,
) -> dict:
    client = ADOMCPClient()
    fields = {"System.State": new_state} if new_state else None
    return await client.update_workitem(workitem_id, fields=fields, comment=comment)
