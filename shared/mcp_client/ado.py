"""Azure DevOps MCP client wrapper.

Connects to the official Azure DevOps MCP server (stdio transport) and exposes
typed convenience methods used by the ADO Developer Assistant and MD Assistant.

Tool names below follow the conventional ADO MCP server tool naming.
Adjust the `tool=` strings to match your specific MCP server build if needed.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ADOMCPClient:
    """Thin async wrapper. One instance per agent process; reuse across calls."""

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        # Defaults assume the official ADO MCP server is installed locally.
        # Override via env: ADO_MCP_COMMAND, ADO_MCP_ARGS (comma-sep)
        cmd = command or os.getenv("ADO_MCP_COMMAND", "npx")
        raw_args = os.getenv("ADO_MCP_ARGS", "@azure-devops/mcp")
        a = args if args is not None else [x.strip() for x in raw_args.split(",") if x.strip()]
        merged_env = {**os.environ, **(env or {})}
        if "ADO_PAT" in merged_env and "AZURE_DEVOPS_PAT" not in merged_env:
            merged_env["AZURE_DEVOPS_PAT"] = merged_env["ADO_PAT"]
        self.params = StdioServerParameters(command=cmd, args=a, env=merged_env)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        async with stdio_client(self.params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s

    # ------------------------------------------------------------------
    # Convenience methods (thin wrappers over MCP tool calls)
    # ------------------------------------------------------------------
    async def list_workitems(
        self,
        *,
        areapath: str,
        iteration: str | None = None,
        assigned_to: str | None = None,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        async with self.session() as s:
            args: dict[str, Any] = {"areapath": areapath}
            if iteration:
                args["iteration"] = iteration
            if assigned_to:
                args["assignedTo"] = assigned_to
            if states:
                args["states"] = states
            res = await s.call_tool("workitems_list", arguments=args)
            return _extract_json(res)

    async def get_workitem(self, workitem_id: int) -> dict[str, Any]:
        async with self.session() as s:
            res = await s.call_tool("workitems_get", arguments={"id": workitem_id})
            return _extract_json(res)

    async def update_workitem(
        self,
        workitem_id: int,
        *,
        fields: dict[str, Any] | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        async with self.session() as s:
            args: dict[str, Any] = {"id": workitem_id}
            if fields:
                args["fields"] = fields
            if comment:
                args["comment"] = comment
            res = await s.call_tool("workitems_update", arguments=args)
            return _extract_json(res)

    async def list_iterations(self, *, project: str, team: str | None = None) -> list[dict[str, Any]]:
        async with self.session() as s:
            args: dict[str, Any] = {"project": project}
            if team:
                args["team"] = team
            res = await s.call_tool("iterations_list", arguments=args)
            return _extract_json(res)


def _extract_json(tool_result: Any) -> Any:
    """MCP tool results are content blocks; pull out the JSON-shaped payload."""
    if hasattr(tool_result, "content"):
        for c in tool_result.content:
            text = getattr(c, "text", None)
            if text:
                import json
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return tool_result
