"""Observability tools — logs, metrics, deploy timeline, user-log ingest (§9.17.1).

Each system tool is individually config-gated (see registry gates): a disabled or
unconfigured tool is removed from the dispatch table for the investigation, so the
planner never attempts it and instead falls back to asking the user for logs/timeline.
``ingest_user_logs`` parses user-supplied text and is available whenever the manual
fallback is on.
"""
from __future__ import annotations

import structlog

from shared.observability import (
    build_log_adapter,
    build_metrics_adapter,
    deployments_enabled,
    get_deployments,
    parse_logs,
)

logger = structlog.get_logger()


def _obs(ctx: dict) -> dict:
    return ctx.get("observability") or {}


async def query_logs_tool(project_id: str, args: dict, ctx: dict) -> str:
    adapter = build_log_adapter(_obs(ctx))
    if adapter is None:
        return ("(log access not enabled — raise ask_user blocks=evidence_request to ask "
                "the reporter to paste the relevant log window)")
    return await adapter.query(
        str(args.get("query", "")), args.get("time_range"), args.get("env"))


async def query_metrics_tool(project_id: str, args: dict, ctx: dict) -> str:
    adapter = build_metrics_adapter(_obs(ctx))
    if adapter is None:
        return ("(metrics access not enabled — ask the reporter roughly when this started / "
                "whether it spikes at a particular time)")
    return await adapter.query(
        str(args.get("metric", "")), args.get("time_range"), args.get("env"))


async def get_deployments_tool(project_id: str, args: dict, ctx: dict) -> str:
    if not deployments_enabled(_obs(ctx)):
        return ("(deploy history not enabled — ask the reporter whether there was a release "
                "shortly before this started, and which build/commit)")
    return await get_deployments(args.get("time_range"), args.get("env"))


async def ingest_user_logs_tool(project_id: str, args: dict, ctx: dict) -> str:
    text = str(args.get("text", "") or args.get("logs", ""))
    if not text.strip():
        return "(ingest_user_logs needs 'text' — the pasted/attached log content)"
    parsed = parse_logs(text, query=args.get("query"))
    return "user-provided logs:\n" + parsed["summary"]
