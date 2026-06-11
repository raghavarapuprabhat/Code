"""ETL phase 5 — LLM-generated key achievements per squad."""
from __future__ import annotations

import asyncio
import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import ETLState
from .etl_metrics import _parse_date

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "achievements.md")
DONE_STATES = {"Done", "Closed", "Resolved", "Completed"}


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


async def generate_achievements_node(state: ETLState, *, config: dict) -> dict:
    cfg = config["md"]
    top_n = int(cfg.get("achievement_top_n", 5))
    template = _load_prompt()
    llm = build_adapter_from_config(config)
    snap = state["snapshot_date"]
    achievements: list[dict] = []

    sem = asyncio.Semaphore(3)

    async def per_squad(squad_name: str, items: list[dict]) -> None:
        closed = [
            {
                "id": i.get("id"),
                "title": i.get("title"),
                "type": i.get("work_item_type"),
                "story_points": i.get("story_points"),
                "closed_date": str(_parse_date(i.get("closed_date")) or ""),
            }
            for i in items
            if i.get("state") in DONE_STATES
        ]
        if not closed:
            return
        prompt = template.format(
            top_n=top_n,
            squad_name=squad_name,
            snapshot_date=snap,
            closed_items_json=json.dumps(closed, indent=2)[:24000],
        )
        async with sem:
            try:
                resp = await llm.chat([{"role": "user", "content": prompt}])
            except Exception as e:  # noqa: BLE001
                logger.exception("achievements_llm_failed", squad=squad_name, err=str(e))
                return
        parsed = _safe_json_array(resp.content) or []
        for a in parsed[:top_n]:
            if not isinstance(a, dict) or not a.get("achievement"):
                continue
            achievements.append(
                {
                    "snapshot_date": snap,
                    "squad_name": squad_name,
                    "achievement": a["achievement"],
                    "evidence_workitem_ids": [int(x) for x in a.get("evidence_workitem_ids", []) if isinstance(x, (int, str)) and str(x).isdigit()],
                }
            )

    await asyncio.gather(*(per_squad(n, items) for n, items in (state.get("workitems_by_squad") or {}).items()))
    logger.info("etl_achievements_done", count=len(achievements))
    return {"achievements": achievements}


def _safe_json_array(text: str):
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("["), text.rfind("]")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
