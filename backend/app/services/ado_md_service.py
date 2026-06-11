"""Bridge from FastAPI to the standalone ADO MD agent."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, AsyncIterator

import structlog

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = structlog.get_logger()


async def get_dashboard(snapshot_date: str | None = None) -> dict[str, Any]:
    from agents.ado_md_agent.tools.snapshot_db import load_dashboard
    return await load_dashboard(snapshot_date)


async def trigger_etl(snapshot_date: str | None = None) -> dict[str, Any]:
    from agents.ado_md_agent.graph import run_etl
    return await run_etl(snapshot_date=snapshot_date)


async def drill_down_stream(
    *,
    question: str,
    squad_filter: str | None,
    snapshot_date: str | None,
) -> AsyncIterator[dict]:
    from agents.ado_md_agent.graph import run_drill

    yield {"type": "start", "question": question}
    try:
        out = await run_drill(
            question=question,
            squad_filter=squad_filter,
            snapshot_date=snapshot_date,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("md_drill_failed")
        yield {"type": "error", "message": str(e)}
        return
    yield {
        "type": "answer",
        "answer": out.get("answer"),
        "citations": out.get("citations"),
        "snapshot_date": out.get("snapshot_date"),
        "used_live": out.get("used_live"),
    }
    yield {"type": "final"}


def derive_attention(dashboard: dict, *, overdue_threshold: int, velocity_drop_pct: float, blocked_threshold: int) -> list[dict]:
    """Pure-Python attention rules — no LLM cost."""
    attention: list[dict] = []
    for s in dashboard.get("squads", []):
        if s.get("overdue", 0) >= overdue_threshold:
            attention.append({
                "squad_name": s["squad_name"],
                "type": "overdue",
                "detail": f"{s['overdue']} workitems past their target date",
            })
        if s.get("blocked", 0) >= blocked_threshold:
            attention.append({
                "squad_name": s["squad_name"],
                "type": "blocked",
                "detail": f"{s['blocked']} workitems are blocked",
            })
    return attention


def attention_for(dashboard: dict, config_attention: dict) -> list[dict]:
    return derive_attention(
        dashboard,
        overdue_threshold=int(config_attention.get("overdue_threshold", 3)),
        velocity_drop_pct=float(config_attention.get("velocity_drop_pct", 20)),
        blocked_threshold=int(config_attention.get("blocked_threshold", 5)),
    )


def load_md_config() -> dict:
    import yaml
    cfg_path = os.path.normpath(os.path.join(_REPO_ROOT, "agents/ado_md_agent/config.yaml"))
    with open(cfg_path) as fh:
        return yaml.safe_load(fh)


# Re-exported for the router so it doesn't double-import json directly.
encode = json.dumps
