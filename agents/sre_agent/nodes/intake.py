"""Phase 1 — extract a structured IssueIntake from raw user text."""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import SREState

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "intake.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


async def intake_node(state: SREState, *, config: dict) -> dict:
    # If issue is already structured (e.g., from CSV row), skip the LLM call.
    if state.get("issue") and state["issue"].get("description"):
        return {}

    raw = state.get("user_message", "") or ""
    if not raw.strip():
        return {"issue": {}}

    llm = build_adapter_from_config(config)
    prompt = _load_prompt().format(raw_text=raw)
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content) or {}
    parsed.setdefault("title", raw.split("\n", 1)[0][:80])
    parsed.setdefault("description", raw)
    logger.info("sre_intake_done", title=parsed.get("title"))
    return {"issue": parsed}


def _safe_json(text: str):
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
