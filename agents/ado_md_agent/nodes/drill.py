"""Drill-down chat nodes — answer ad-hoc MD questions against snapshot + live MCP."""
from __future__ import annotations

import json
import os
import re

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import DrillState
from ..tools.ado_fetch import fetch_workitems_for_squad
from ..tools.snapshot_db import latest_snapshot_date, load_dashboard

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "drill.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


# Lightweight intent detection — keywords that indicate the user wants live data.
_LIVE_KEYWORDS = ("right now", "today", "currently", "live", "latest", "real-time", "real time")


async def load_snapshot_node(state: DrillState, *, config: dict) -> dict:
    snap = state.get("snapshot_date") or await latest_snapshot_date()
    dash = await load_dashboard(snap)
    return {"snapshot_date": dash["snapshot_date"], "snapshot": dash}


async def maybe_live_query_node(state: DrillState, *, config: dict) -> dict:
    """If the question demands fresh data and a single squad is in scope, hit MCP."""
    q = (state.get("user_question") or "").lower()
    needs_live = any(k in q for k in _LIVE_KEYWORDS)
    if not needs_live:
        return {"live_extra": {}}

    squad_filter = state.get("squad_filter")
    if not squad_filter:
        squad_filter = _guess_squad_from_question(state.get("user_question") or "", config)
    if not squad_filter:
        return {"live_extra": {}}

    squads = config["ado"].get("squads") or []
    match = next((s for s in squads if s["name"].lower() == squad_filter.lower()), None)
    if not match:
        return {"live_extra": {}}

    items = await fetch_workitems_for_squad(
        areapath=match["areapath"],
        iteration=config["ado"].get("current_iteration_token"),
    )
    return {
        "live_extra": {
            "squad_name": match["name"],
            "areapath": match["areapath"],
            "workitem_count": len(items),
            "workitems": items[:25],          # cap for token cost
        }
    }


async def synthesize_answer_node(state: DrillState, *, config: dict) -> dict:
    template = _load_prompt()
    llm = build_adapter_from_config(config)
    prompt = template.format(
        question=state.get("user_question", ""),
        snapshot_date=state.get("snapshot_date") or "(none)",
        snapshot_json=json.dumps(state.get("snapshot") or {}, default=str)[:30000],
        live_json=json.dumps(state.get("live_extra") or {}, default=str)[:15000],
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    answer = resp.content.strip()
    citations = _extract_workitem_ids(answer)
    logger.info("md_drill_synthesized", citations=len(citations))
    return {"answer": answer, "citations": citations}


def _guess_squad_from_question(q: str, config: dict) -> str | None:
    for s in config["ado"].get("squads") or []:
        if s["name"].lower() in q.lower():
            return s["name"]
    return None


def _extract_workitem_ids(text: str) -> list[dict]:
    ids = sorted({int(m) for m in re.findall(r"#?(\d{3,7})", text)})
    return [{"workitem_id": i} for i in ids[:30]]
