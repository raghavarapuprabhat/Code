"""Observability adapters (architecture §9.17.1).

Pluggable, **config-gated** sources for logs / metrics / deploy history. Each is
optional — when disabled or its backend/credentials are missing it reports
unavailable, and the registry removes the corresponding tool from the dispatch table
so the planner never wastes a step (and falls back to asking the user).

For the local POC only the **file** log adapter is fully implemented; App Insights /
Elasticsearch / Splunk / Prometheus are interface stubs that report "not configured"
until wired with credentials. Deploy history uses the existing ADO MCP client.
"""
from __future__ import annotations

import glob
import os

import structlog

from .parser import parse_logs

logger = structlog.get_logger()


# --- logs -------------------------------------------------------------------

class FileLogAdapter:
    """Search local log files (glob) — the always-available POC adapter."""

    def __init__(self, path_glob: str):
        self.path_glob = path_glob

    async def query(self, query: str, time_range: str | None, env: str | None) -> str:
        files = sorted(glob.glob(self.path_glob, recursive=True))
        if not files:
            return f"(no log files matched {self.path_glob})"
        chunks = []
        for f in files[:10]:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    chunks.append(fh.read()[-200_000:])
            except OSError:
                continue
        parsed = parse_logs("\n".join(chunks), query=query)
        return f"logs ({len(files)} file(s), env={env or '?'}):\n{parsed['summary']}"


class _StubAdapter:
    """A configured-but-not-wired adapter (App Insights / ES / Splunk / Prometheus)."""

    def __init__(self, name: str):
        self.name = name

    async def query(self, *_a, **_k) -> str:
        return f"({self.name} adapter is selected but not configured with credentials in this POC)"


def build_log_adapter(obs_cfg: dict):
    logs = (obs_cfg or {}).get("logs", {}) or {}
    if not logs.get("enabled"):
        return None
    adapter = logs.get("adapter", "file")
    if adapter == "file":
        return FileLogAdapter(logs.get("path_glob") or os.getenv("SRE_LOG_GLOB", "./logs/**/*.log"))
    return _StubAdapter(adapter)


def build_metrics_adapter(obs_cfg: dict):
    metrics = (obs_cfg or {}).get("metrics", {}) or {}
    if not metrics.get("enabled"):
        return None
    return _StubAdapter(metrics.get("adapter", "prometheus"))


def deployments_enabled(obs_cfg: dict) -> bool:
    return bool(((obs_cfg or {}).get("deployments", {}) or {}).get("enabled"))


async def get_deployments(time_range: str | None, env: str | None = None) -> str:
    """Recent releases from ADO Pipelines (best-effort; degrades if MCP unavailable)."""
    try:
        from shared.mcp_client.ado import ADOMCPClient
    except Exception as e:  # noqa: BLE001
        return f"(deploy history unavailable: {e})"
    try:
        client = ADOMCPClient()
        if hasattr(client, "list_deployments"):
            rows = await client.list_deployments(time_range=time_range, environment=env)  # type: ignore[attr-defined]
            if not rows:
                return "(no deployments found in the window)"
            return "deployments:\n" + "\n".join(
                f"  - build {r.get('id')} {r.get('finishTime', '')} commit {r.get('commit', '')[:8]} ({r.get('env','')})"
                for r in rows[:10]
            )
        return "(ADO MCP client has no list_deployments tool wired in this build)"
    except Exception as e:  # noqa: BLE001
        return f"(deploy history unavailable: {e})"
