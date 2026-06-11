"""Verify-after-fix graph (§9.17.4).

Lightweight graph triggered after the Fixer's PR is merged and deployed.
Re-runs the probes that were taken during the original triage; compares results to
the original evidence to confirm the symptoms have resolved.

Returns either:
  "verified"      — none of the original probe observations show the failure pattern
  "still_failing" — at least one probe still matches the original failure signature

The graph is intentionally thin — no ReAct loop, no hypothesis board.  It is a
read-only re-check of the exact signals the SRE investigator used to confirm the bug.
"""
from __future__ import annotations

import json
import os
import re
from functools import partial
from typing import AsyncIterator

import structlog
import yaml
from langgraph.graph import END, StateGraph

from .nodes.investigate import _budget           # reuse budget structure
from .state import SREState
from .tools import tool_catalog
from .tools.registry import available_tools

logger = structlog.get_logger()

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


async def _verify_node(state: SREState, *, config: dict) -> dict:
    """Re-run each probe from the original probe_log; compare to original evidence."""
    from shared.llm_adapter import build_adapter_from_config

    probe_log = list(state.get("probe_log") or [])
    evidence = list(state.get("evidence") or [])
    verdict = state.get("verdict") or {}
    original_root_cause = verdict.get("root_cause", "")

    if not probe_log:
        # No probes were taken originally; use LLM to assess from re-read evidence only.
        logger.info("verify_no_probes", conversation_id=state.get("conversation_id"))
        return {
            "verdict": {
                **verdict,
                "classification": "needs_more_info",
                "rationale": "No probes were taken during original triage; cannot re-verify.",
            }
        }

    sre_cfg = config.get("sre", {}) or {}
    tools = available_tools(config)
    ctx = {
        "facts": state.get("facts") or {},
        "conversation_id": state.get("conversation_id"),
        "budget": _budget(config, batch=False),
        "prod_approved": True,      # verify always runs approved (we already probed in triage)
        "adhoc_targets": state.get("adhoc_targets") or [],
        "environments_path": (sre_cfg.get("probes", {}) or {}).get("environments_path") or None,
        "observability": sre_cfg.get("observability", {}) or {},
    }
    project_id = state.get("project_id", "")

    re_observations: list[dict] = []
    for entry in probe_log[:6]:          # cap at 6 re-probes
        tool_name = entry.get("tool")
        if tool_name not in tools:
            continue
        # Reconstruct args from the probe_log entry.
        args: dict = {}
        if entry.get("target"):
            args["target"] = entry["target"]
        if entry.get("environment"):
            args["environment"] = entry["environment"]
        try:
            obs = await tools[tool_name](project_id, args, ctx)
        except Exception as exc:  # noqa: BLE001
            obs = f"(probe failed: {exc})"
        re_observations.append({
            "tool": tool_name,
            "target": entry.get("target"),
            "env": entry.get("environment"),
            "observation": (obs or "")[:800],
        })

    # Ask the LLM to compare: do the re-observations still show the original failure?
    llm = build_adapter_from_config(config)
    compare_prompt = (
        "You are verifying whether a bug fix resolved the original issue.\n\n"
        f"Original root cause: {original_root_cause[:500]}\n\n"
        "Original probe observations (before fix):\n"
        + "\n".join(
            f"  [{e.get('source','?')}] {e.get('citation','')} — {e.get('finding','')[:300]}"
            for e in evidence[-10:]
        )
        + "\n\nNew probe observations (after fix):\n"
        + "\n".join(
            f"  [{o['tool']}@{o.get('env','')}] {o['target']}: {o['observation'][:300]}"
            for o in re_observations
        )
        + '\n\nDoes the new data still show the original failure? '
          'Reply with exactly one JSON object:\n'
          '{"still_failing": true|false, "confidence": 0-1, "summary": "one sentence"}'
    )
    resp = await llm.chat([{"role": "user", "content": compare_prompt}])
    decision = _safe_json(resp.content) or {"still_failing": True, "confidence": 0.5, "summary": "Could not parse LLM assessment."}

    status = "still_failing" if decision.get("still_failing") else "verified"
    logger.info(
        "verify_done",
        status=status,
        confidence=decision.get("confidence"),
        conversation_id=state.get("conversation_id"),
    )
    return {
        "verdict": {
            **verdict,
            "verification_status": status,
            "verification_confidence": decision.get("confidence", 0.5),
            "verification_summary": decision.get("summary", ""),
            "re_observations": re_observations,
        }
    }


def _safe_json(text: str):
    text = (text or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None


def build_verify_graph(config: dict | None = None):
    cfg = config or load_config()
    g = StateGraph(SREState)
    g.add_node("verify", partial(_verify_node, config=cfg))
    g.set_entry_point("verify")
    g.add_edge("verify", END)
    return g.compile()


async def run_verify_fix(
    *,
    conversation_id: str,
    project_id: str,
    pr_url: str | None = None,
    config: dict | None = None,
) -> dict:
    """Entry point called by the backend service.

    Reads the original triage state from the interactive graph's checkpointer,
    re-runs probes, and returns a verification result dict.
    The caller is responsible for posting the result back to the PR thread.
    """
    from .graph import get_interactive_graph

    cfg = config or load_config()
    orig_graph = get_interactive_graph()
    gcfg = {"configurable": {"thread_id": conversation_id}}

    try:
        snap = await orig_graph.aget_state(gcfg)
        original_state = dict(snap.values or {}) if snap else {}
    except Exception:  # noqa: BLE001
        original_state = {}

    initial: dict = {
        **original_state,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "prod_probe_approved": True,   # already approved during original triage
    }

    verify_graph = build_verify_graph(cfg)
    result = await verify_graph.ainvoke(initial)
    verdict = result.get("verdict") or {}
    status = verdict.get("verification_status", "unknown")
    summary = verdict.get("verification_summary", "")

    icon = "✅" if status == "verified" else "❌"
    message = f"{icon} SRE verify-after-fix: **{status}**. {summary}"
    if pr_url:
        message += f"\n\nOriginal triage: conversation `{conversation_id}`"

    return {
        "conversation_id": conversation_id,
        "status": status,
        "confidence": verdict.get("verification_confidence", 0.0),
        "summary": summary,
        "message": message,
        "pr_url": pr_url,
    }
