"""Azure DevOps MCP client wrapper.

Connects to the official Azure DevOps MCP server (`@azure-devops/mcp`, stdio transport)
and exposes typed convenience methods used by the ADO Developer Assistant, MD Assistant,
the Code Doc Agent's requirements ingest, and the SRE Agent's bug write-back.

Tool names + argument shapes follow the **real** `@azure-devops/mcp` server contract
(see microsoft/azure-devops-mcp docs/TOOLSET.md):
  wit_query_by_wiql            (wiql, project?)            -> work-item *references* (ids)
  wit_get_work_items_batch_by_ids (project, ids)          -> fully-hydrated items (fields)
  wit_get_work_item            (id, project)              -> one hydrated item
  wit_create_work_item         (project, workItemType, fields)
  wit_update_work_item         (id, updates)              -> JSON-Patch ops
  work_list_iterations         (project)

The server has no "list by area path" tool, so ``list_workitems`` is implemented as a
WIQL query (filtering on System.AreaPath) followed by a batch hydration — callers still
get items with a ``fields`` dict, exactly as before.

Override the spawned server via env: ADO_MCP_COMMAND / ADO_MCP_ARGS (comma-sep). The
default project for WIQL/get/create comes from ADO_PROJECT when a caller doesn't pass one.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_BATCH_FIELDS = [
    "System.Id", "System.Title", "System.State", "System.WorkItemType",
    "System.AssignedTo", "System.AreaPath", "System.IterationPath", "System.Tags",
    "System.Description", "System.ChangedDate",
    "Microsoft.VSTS.Scheduling.StoryPoints", "Microsoft.VSTS.Scheduling.TargetDate",
    "Microsoft.VSTS.Scheduling.StartDate", "Microsoft.VSTS.Common.ClosedDate",
]


def _esc(s: str) -> str:
    """Escape a value for embedding in a WIQL single-quoted string literal."""
    return (s or "").replace("'", "''")


class ADOMCPClient:
    """Thin async wrapper. One instance per agent process; reuse across calls."""

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        project: str | None = None,
    ):
        # Defaults assume the official ADO MCP server is launched via npx.
        # Override via env: ADO_MCP_COMMAND, ADO_MCP_ARGS (comma-sep).
        cmd = command or os.getenv("ADO_MCP_COMMAND", "npx")
        raw_args = os.getenv("ADO_MCP_ARGS", "@azure-devops/mcp")
        a = args if args is not None else [x.strip() for x in raw_args.split(",") if x.strip()]
        merged_env = {**os.environ, **(env or {})}
        if "ADO_PAT" in merged_env and "AZURE_DEVOPS_PAT" not in merged_env:
            merged_env["AZURE_DEVOPS_PAT"] = merged_env["ADO_PAT"]
        self.params = StdioServerParameters(command=cmd, args=a, env=merged_env)
        # Default project for tools that require it (the dev/md callers pass area path,
        # not project, so we fall back to ADO_PROJECT).
        self.default_project = project or os.getenv("ADO_PROJECT", "")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        async with stdio_client(self.params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s

    def _project(self, project: str | None) -> str:
        return project or self.default_project

    # ------------------------------------------------------------------
    # Work items
    # ------------------------------------------------------------------
    async def list_workitems(
        self,
        *,
        areapath: str,
        iteration: str | None = None,
        assigned_to: str | None = None,
        states: list[str] | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """List work items under an area path (WIQL → batch hydrate).

        Returns fully-hydrated items (each with a ``fields`` dict), matching what the
        dev/md/requirements callers already expect.
        """
        proj = self._project(project)
        clauses = [f"[System.AreaPath] UNDER '{_esc(areapath)}'"]
        if iteration:
            clauses.append(f"[System.IterationPath] UNDER '{_esc(iteration)}'")
        if assigned_to:
            clauses.append(f"[System.AssignedTo] = '{_esc(assigned_to)}'")
        if states:
            ors = " OR ".join(f"[System.State] = '{_esc(st)}'" for st in states)
            clauses.append(f"({ors})")
        wiql = (
            "SELECT [System.Id] FROM WorkItems WHERE "
            + " AND ".join(clauses)
            + " ORDER BY [System.ChangedDate] DESC"
        )
        async with self.session() as s:
            return await self._query_and_hydrate(s, wiql=wiql, project=proj)

    async def search_workitems(
        self, *, project: str | None = None, wiql: str
    ) -> list[dict[str, Any]]:
        """Run an arbitrary WIQL query and return hydrated work items."""
        async with self.session() as s:
            return await self._query_and_hydrate(s, wiql=wiql, project=self._project(project))

    async def get_workitem(
        self, workitem_id: int, *, project: str | None = None
    ) -> dict[str, Any]:
        async with self.session() as s:
            res = await s.call_tool(
                "wit_get_work_item",
                arguments={"id": int(workitem_id), "project": self._project(project)},
            )
            return _extract_json(res)

    async def create_workitem(
        self,
        *,
        project: str,
        work_item_type: str = "Bug",
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        area_path: str | None = None,
        assigned_to: str | None = None,
        priority: int = 2,
    ) -> dict[str, Any]:
        """Create a new work item (Bug by default) in the given project."""
        fields: dict[str, Any] = {
            "System.Title": title,
            "Microsoft.VSTS.Common.Priority": priority,
        }
        if description:
            fields["System.Description"] = description
        if tags:
            fields["System.Tags"] = "; ".join(tags)
        if area_path:
            fields["System.AreaPath"] = area_path
        if assigned_to:
            fields["System.AssignedTo"] = assigned_to
        async with self.session() as s:
            res = await s.call_tool(
                "wit_create_work_item",
                arguments={
                    "project": self._project(project),
                    "workItemType": work_item_type,
                    "fields": fields,
                },
            )
            return _extract_json(res)

    async def update_workitem(
        self,
        workitem_id: int,
        *,
        fields: dict[str, Any] | None = None,
        comment: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Update a work item via JSON-Patch ops (the server's `updates` contract).

        ``fields`` (System.* -> value) become `add`/`replace` field ops; ``comment`` is
        added via the dedicated comment tool (System.History over a patch is unreliable).
        """
        updates = [
            {"op": "add", "path": f"/fields/{name}", "value": value}
            for name, value in (fields or {}).items()
        ]
        result: dict[str, Any] = {}
        async with self.session() as s:
            if updates:
                res = await s.call_tool(
                    "wit_update_work_item",
                    arguments={"id": int(workitem_id), "updates": updates},
                )
                result = _extract_json(res) or {}
            if comment:
                await s.call_tool(
                    "wit_add_work_item_comment",
                    arguments={
                        "project": self._project(project),
                        "workItemId": int(workitem_id),
                        "comment": comment,
                    },
                )
        return result

    # ------------------------------------------------------------------
    # Iterations
    # ------------------------------------------------------------------
    async def list_iterations(
        self, *, project: str, team: str | None = None
    ) -> list[dict[str, Any]]:
        async with self.session() as s:
            # work_list_team_iterations when a team is given; otherwise project-wide.
            if team:
                res = await s.call_tool(
                    "work_list_team_iterations",
                    arguments={"project": self._project(project), "team": team},
                )
            else:
                res = await s.call_tool(
                    "work_list_iterations",
                    arguments={"project": self._project(project)},
                )
            out = _extract_json(res)
            if isinstance(out, dict):
                return out.get("value") or out.get("iterations") or []
            return out or []

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _query_and_hydrate(
        self, s: ClientSession, *, wiql: str, project: str
    ) -> list[dict[str, Any]]:
        """WIQL → ids → batch-get hydrated items. WIQL alone returns id references only."""
        args: dict[str, Any] = {"wiql": wiql}
        if project:
            args["project"] = project
        res = await s.call_tool("wit_query_by_wiql", arguments=args)
        refs = _extract_json(res)
        ids = _ids_from_wiql(refs)
        if not ids:
            return []
        # Batch in chunks of 200 (the ADO API cap).
        items: list[dict[str, Any]] = []
        for i in range(0, len(ids), 200):
            batch = ids[i : i + 200]
            bres = await s.call_tool(
                "wit_get_work_items_batch_by_ids",
                arguments={"project": project, "ids": batch, "fields": _BATCH_FIELDS},
            )
            hydrated = _extract_json(bres)
            items.extend(_items_from_batch(hydrated))
        return items


def _ids_from_wiql(result: Any) -> list[int]:
    """Pull work-item ids out of a wit_query_by_wiql response."""
    if isinstance(result, dict):
        refs = result.get("workItems") or result.get("value") or []
    elif isinstance(result, list):
        refs = result
    else:
        return []
    ids: list[int] = []
    for r in refs:
        if isinstance(r, dict) and r.get("id") is not None:
            ids.append(int(r["id"]))
        elif isinstance(r, int):
            ids.append(r)
    return ids


def _items_from_batch(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        return result.get("value") or result.get("workItems") or []
    if isinstance(result, list):
        return result
    return []


def _extract_json(tool_result: Any) -> Any:
    """MCP tool results are content blocks; pull out the JSON-shaped payload."""
    if hasattr(tool_result, "content"):
        for c in tool_result.content:
            text = getattr(c, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return tool_result
