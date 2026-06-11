"""ADO MCP fetch helpers used by both the ETL job and the live drill-down.

Wraps the existing shared.mcp_client.ADOMCPClient with shapes the MD agent needs.
Returns plain dicts (not WorkItem models) to keep the LangGraph state JSON-safe.
"""
from __future__ import annotations

from typing import Any

import structlog

from shared.mcp_client import ADOMCPClient

logger = structlog.get_logger()


async def fetch_workitems_for_squad(
    *,
    areapath: str,
    iteration: str | None = None,
    states: list[str] | None = None,
) -> list[dict[str, Any]]:
    client = ADOMCPClient()
    try:
        items = await client.list_workitems(
            areapath=areapath,
            iteration=iteration,
            states=states,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("mcp_list_failed", areapath=areapath, err=str(e))
        return []
    if not isinstance(items, list):
        # Some MCP implementations wrap results in {"value": [...]}
        if isinstance(items, dict) and isinstance(items.get("value"), list):
            items = items["value"]
        else:
            return []
    return [_normalize(item) for item in items]


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Map common ADO field names into our flat schema."""
    f = raw.get("fields") or raw  # MCP servers vary in nesting
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
        "closed_date": f.get("Microsoft.VSTS.Common.ClosedDate") or raw.get("closed_date"),
        "created_date": f.get("System.CreatedDate") or raw.get("created_date"),
        "work_item_type": f.get("System.WorkItemType") or raw.get("work_item_type", ""),
    }


def _person(field: Any) -> str | None:
    if not field:
        return None
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return field.get("displayName") or field.get("uniqueName")
    return None


def _tags(field: Any) -> list[str]:
    if not field:
        return []
    if isinstance(field, list):
        return [str(t).strip() for t in field if str(t).strip()]
    if isinstance(field, str):
        return [t.strip() for t in field.split(";") if t.strip()]
    return []
