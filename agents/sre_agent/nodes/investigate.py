"""Phase 4 — Investigate: the ReAct loop (reason → act → observe → reflect, §9.9).

Each iteration is a single LLM call that (a) interprets the previous observation into
Evidence and re-scores the hypothesis board, then (b) either picks the one tool call
that best reduces uncertainty or stops. The loop runs under a hard budget; stopping is
honest — confident, out of budget, or no new evidence — never fabricated certainty.

Live probes and mid-loop ``ask_user`` (interrupt) are out of scope for this static-tool
foundation; they slot into the same loop in the v0.4/v0.6 phases.
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import SREState
from ..tools import tool_catalog
from ..tools.registry import available_tools

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "plan.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


def _budget(config: dict, batch: bool) -> dict:
    b = dict((config.get("sre", {}) or {}).get("budget", {}) or {})
    out = {
        "max_steps": int(b.get("max_steps", 8)),
        "max_tool_calls": int(b.get("max_tool_calls", 16)),
        "max_tokens": int(b.get("max_tokens", 60_000)),
        "used_steps": 0,
        "used_tool_calls": 0,
    }
    if batch:
        bb = b.get("batch", {}) or {}
        out["max_steps"] = int(bb.get("max_steps", 3))
        out["max_tool_calls"] = int(bb.get("max_tool_calls", 4))
    return out


def _render_hypotheses(hyps: list[dict]) -> str:
    if not hyps:
        return "(no hypotheses)"
    rows = sorted(hyps, key=lambda h: h.get("posterior", 0), reverse=True)
    return "\n".join(
        f"  {h['id']} [{h.get('status', 'open')} p={h.get('posterior', 0):.2f}] {h['statement']}"
        for h in rows
    )


def _render_evidence(ev: list[dict]) -> str:
    if not ev:
        return "(no evidence yet)"
    return "\n".join(
        f"  {e['id']} ({e['source']}) {e['citation']}: {e['finding'][:160]} "
        f"[bears_on: {', '.join(e.get('bears_on', [])) or '-'}]"
        for e in ev
    )


def _render_scratchpad(log: list[dict], keep: int = 6) -> str:
    if not log:
        return "(no steps taken yet — pick your first action)"
    out = []
    for s in log[-keep:]:
        out.append(f"Step {s['n']}: {s.get('thought', '')}")
        out.append(f"  Action: {s.get('action', '')}")
        out.append(f"  Observation: {s.get('observation', '')[:800]}")
    return "\n".join(out)


def _confident(hyps: list[dict], threshold: float) -> bool:
    if not hyps:
        return False
    ranked = sorted(hyps, key=lambda h: h.get("posterior", 0), reverse=True)
    top = ranked[0].get("posterior", 0)
    rival = ranked[1].get("posterior", 0) if len(ranked) > 1 else 0.0
    return top >= threshold and (top - rival) >= 0.15


async def investigate_node(state: SREState, *, config: dict) -> dict:
    sre_cfg = config.get("sre", {}) or {}
    threshold = float(sre_cfg.get("confidence_threshold", 0.7))
    batch = bool(state.get("batch"))

    budget = dict(state.get("budget") or _budget(config, batch))
    hypotheses = [dict(h) for h in (state.get("hypotheses") or [])]
    evidence = [dict(e) for e in (state.get("evidence") or [])]
    log = [dict(s) for s in (state.get("investigation_log") or [])]

    tools = available_tools(config, batch=batch)
    catalog = tool_catalog(list(tools.keys()))
    ctx = {"facts": state.get("facts") or {}, "conversation_id": state.get("conversation_id")}

    llm = build_adapter_from_config(config)
    template = _load_prompt()

    last_observation = ""
    no_progress = 0
    stop_reason = "budget"

    while budget["used_steps"] < budget["max_steps"] and budget["used_tool_calls"] < budget["max_tool_calls"]:
        prompt = (
            template
            .replace("{facts_json}", json.dumps(state.get("facts") or {}, indent=2))
            .replace("{hypotheses_block}", _render_hypotheses(hypotheses))
            .replace("{evidence_block}", _render_evidence(evidence))
            .replace("{scratchpad}", _render_scratchpad(log))
            .replace("{tool_catalog}", catalog)
            .replace("{steps_left}", str(budget["max_steps"] - budget["used_steps"]))
            .replace("{last_observation}", last_observation or "(none yet)")
        )
        resp = await llm.chat([{"role": "user", "content": prompt}])
        decision = _safe_json(resp.content)
        if decision is None:
            logger.warning("investigate_unparseable_decision")
            stop_reason = "no_new_evidence"
            break

        # 1. Fold the last observation into evidence + re-score hypotheses.
        progressed = _apply_evidence(decision.get("evidence", []), evidence, hypotheses)
        progressed |= _apply_updates(decision.get("hypothesis_updates", []), hypotheses)

        # 2. Confident enough? Stop before spending another tool call.
        if _confident(hypotheses, threshold):
            stop_reason = "confident"
            break

        action = (decision.get("action") or "tool").lower()
        thought = decision.get("thought", "")
        if action == "stop":
            stop_reason = decision.get("stop_reason", "no_new_evidence")
            log.append({"n": budget["used_steps"] + 1, "thought": thought, "action": "stop", "observation": ""})
            budget["used_steps"] += 1
            break

        tool_name = decision.get("tool", "")
        args = decision.get("args", {}) or {}
        if tool_name not in tools:
            observation = f"(tool '{tool_name}' is not available in this run)"
        else:
            try:
                observation = await tools[tool_name](state.get("project_id", ""), args, ctx)
            except Exception as e:  # noqa: BLE001 — a tool failure is an observation, not a crash
                observation = f"(tool '{tool_name}' failed: {e})"
            budget["used_tool_calls"] += 1
        observation = (observation or "")[:1500]

        budget["used_steps"] += 1
        log.append(
            {
                "n": budget["used_steps"],
                "thought": thought,
                "action": f"{tool_name}({json.dumps(args)})",
                "observation": observation,
            }
        )
        last_observation = observation

        # No-progress guard: two consecutive non-informative steps → stop digging.
        no_progress = 0 if progressed else no_progress + 1
        if no_progress >= 2:
            stop_reason = "no_new_evidence"
            break

    logger.info(
        "investigate_done",
        steps=budget["used_steps"],
        tool_calls=budget["used_tool_calls"],
        evidence=len(evidence),
        stop=stop_reason,
    )
    return {
        "hypotheses": hypotheses,
        "evidence": evidence,
        "investigation_log": log,
        "budget": budget,
    }


def _apply_evidence(items: list[dict], evidence: list[dict], hyps: list[dict]) -> bool:
    changed = False
    by_id = {h["id"]: h for h in hyps}
    for it in items or []:
        if not it.get("finding") and not it.get("citation"):
            continue
        eid = f"E{len(evidence) + 1}"
        bears = [b for b in (it.get("bears_on") or []) if b in by_id]
        row = {
            "id": eid,
            "source": it.get("source", "code"),
            "citation": it.get("citation", ""),
            "finding": it.get("finding", ""),
            "bears_on": bears,
        }
        evidence.append(row)
        effect = (it.get("effect") or "").lower()
        for hid in bears:
            h = by_id[hid]
            if effect == "supports":
                h.setdefault("supporting", []).append(eid)
            elif effect == "refutes":
                h.setdefault("refuting", []).append(eid)
        changed = True
    return changed


def _apply_updates(updates: list[dict], hyps: list[dict]) -> bool:
    changed = False
    by_id = {h["id"]: h for h in hyps}
    for u in updates or []:
        h = by_id.get(u.get("id"))
        if not h:
            continue
        if "posterior" in u:
            try:
                new_p = max(0.0, min(1.0, float(u["posterior"])))
                if abs(new_p - h.get("posterior", 0)) > 1e-6:
                    changed = True
                h["posterior"] = new_p
            except (TypeError, ValueError):
                pass
        if u.get("status") in {"open", "supported", "refuted"}:
            if u["status"] != h.get("status"):
                changed = True
            h["status"] = u["status"]
    return changed


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
