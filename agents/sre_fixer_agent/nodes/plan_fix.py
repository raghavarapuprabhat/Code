"""Phase 2 — produce a FixPlan via the LLM."""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import FixerState
from ..tools.patch_tools import read_file

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "plan_fix.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


async def plan_fix_node(state: FixerState, *, config: dict) -> dict:
    cfg = config["fixer"]
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    handoff = state.get("handoff") or {}
    verdict = handoff.get("verdict") or {}
    likely = verdict.get("likely_files") or handoff.get("likely_files") or []
    repo_path = state["repo_path"]

    files_block_parts = []
    for rel in likely[:6]:
        content = read_file(repo_path, rel)
        if not content:
            continue
        files_block_parts.append(f"### {rel}\n```\n{content[:12000]}\n```")
    if not files_block_parts:
        files_block_parts.append("(no candidate file contents available)")

    prev_block = "(this is the first attempt)"
    history = state.get("plan_history") or []
    last_test = state.get("last_test")
    failure_analysis = state.get("failure_analysis")
    if history and last_test:
        prev_block = json.dumps({
            "previous_plan": history[-1],
            "previous_test_result": {
                "passed": last_test.get("passed"),
                "failed_tests": last_test.get("failed_tests"),
                "stderr_tail": last_test.get("stderr_tail"),
            },
            "failure_analysis": failure_analysis or "",
        }, indent=2)[:18000]

    prompt = template.format(
        allowed_test_keys=", ".join(cfg["test_commands"].keys()),
        verdict_json=json.dumps({"issue": handoff.get("issue"), "verdict": verdict}, indent=2),
        files_block="\n\n".join(files_block_parts),
        previous_attempt_block=prev_block,
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    plan = _safe_json(resp.content)

    if not plan or not isinstance(plan.get("edits"), list) or not plan["edits"]:
        msg = "Planner returned no edits; raising for human."
        logger.error("fixer_plan_invalid", err=msg, raw=resp.content[:500])
        return {
            "status": "raised_human",
            "error": msg,
            "audit_trail": (state.get("audit_trail") or []) + [
                {"step": "plan_fix", "status": "invalid", "detail": msg}
            ],
        }

    plan_history = list(state.get("plan_history") or []) + [plan]
    logger.info(
        "fixer_plan_ready",
        attempt=state.get("attempt"),
        edits=len(plan["edits"]),
        test_key=plan.get("test_command_key"),
    )
    return {
        "plan": plan,
        "plan_history": plan_history,
        "audit_trail": (state.get("audit_trail") or []) + [
            {"step": "plan_fix", "status": "ok", "edits": len(plan["edits"])}
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
