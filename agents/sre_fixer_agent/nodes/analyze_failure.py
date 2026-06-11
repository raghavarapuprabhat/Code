"""Phase 5 — when tests fail, decide whether to retry the plan or raise to a human."""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import FixerState

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "analyze_failure.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


async def analyze_failure_node(state: FixerState, *, config: dict) -> dict:
    cfg = config["fixer"]
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    plan = state.get("plan") or {}
    last_test = state.get("last_test") or {}
    output = (last_test.get("stderr_tail") or "") + "\n" + (last_test.get("stdout_tail") or "")

    prompt = template.format(
        plan_json=json.dumps(plan, indent=2)[:18000],
        command=" ".join(last_test.get("command") or []),
        failed_tests="\n".join(last_test.get("failed_tests") or []) or "(none parsed)",
        output_tail=output[-12000:],
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    analysis = _safe_json(resp.content) or {
        "caused_by_patch": True,
        "should_retry": False,
        "summary": "Failure analyzer returned unparseable output; defaulting to raise human.",
        "files_to_revisit": [],
        "new_hypothesis": "",
    }

    attempts = int(state.get("attempt", 0))
    max_attempts = int(cfg.get("max_fix_attempts", 3))
    out_status = "raised_human"
    if analysis.get("should_retry") and attempts < max_attempts:
        out_status = "planning"

    logger.info(
        "fixer_failure_analyzed",
        should_retry=analysis.get("should_retry"),
        attempts=attempts,
        next_status=out_status,
    )
    return {
        "failure_analysis": analysis.get("summary", ""),
        "status": out_status,
        "audit_trail": (state.get("audit_trail") or []) + [
            {
                "step": "analyze_failure",
                "should_retry": analysis.get("should_retry"),
                "summary": analysis.get("summary", ""),
                "attempt": attempts,
            }
        ],
    }


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
