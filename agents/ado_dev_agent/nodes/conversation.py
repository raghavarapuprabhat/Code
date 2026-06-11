"""Conversational nodes: route based on `step`, ask the next question, parse the answer.

Each node returns the partial state delta for this turn, including the next
`step` so the backend can persist it and replay on the next user message.
"""
from __future__ import annotations

import json
from datetime import date

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import DevState
from ..tools.prefs import load_prefs, save_prefs
from ..tools.workitems import compute_status, list_assigned, update_workitem
from ._helpers import load_prompt, parse_json_array, parse_json_object

logger = structlog.get_logger()


# ----------------------------------------------------------------------
# Greet — first turn only
# ----------------------------------------------------------------------
async def greet_node(state: DevState, *, config: dict) -> dict:
    user_id = state["user_id"]
    prefs = await load_prefs(user_id)
    saved = prefs.get("last_areapath")
    saved_iter = prefs.get("last_iteration")
    if saved:
        msg = (
            f"Hi! Use the areapath '{saved}'"
            f"{' (iteration ' + saved_iter + ')' if saved_iter else ''}? "
            "Reply 'yes' or give me a different areapath."
        )
    else:
        msg = (
            "Hi! What ADO areapath should I work with? "
            "(e.g. `MyProject\\TeamA`). You can also give me an iteration."
        )
    return {
        "step": "await_areapath",
        "last_areapath": saved,
        "last_iteration": saved_iter,
        "response_text": msg,
        "needs_consent": False,
    }


# ----------------------------------------------------------------------
# Areapath confirmation
# ----------------------------------------------------------------------
async def handle_areapath_node(state: DevState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    prompt = load_prompt("areapath.md").format(
        saved=state.get("last_areapath") or "",
        saved_iter=state.get("last_iteration") or "",
        user_message=state.get("user_message", ""),
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = parse_json_object(resp.content) or {}

    if parsed.get("unclear"):
        return {
            "step": "await_areapath",
            "response_text": parsed.get("ask")
            or "Sorry, can you give me the areapath in `Project\\Team\\Sub` form?",
        }

    if parsed.get("keep") and state.get("last_areapath"):
        return await _ask_intent(state)

    new_path = parsed.get("areapath") or state.get("last_areapath")
    new_iter = parsed.get("iteration") or state.get("last_iteration")
    if not new_path:
        return {
            "step": "await_areapath",
            "response_text": "I still need an areapath. Please send it in `Project\\Team\\Sub` form.",
        }

    await save_prefs(state["user_id"], last_areapath=new_path, last_iteration=new_iter)
    out = await _ask_intent({**state, "last_areapath": new_path, "last_iteration": new_iter})
    out["last_areapath"] = new_path
    out["last_iteration"] = new_iter
    return out


async def _ask_intent(state: DevState) -> dict:
    iter_str = f" (iteration {state.get('last_iteration')})" if state.get("last_iteration") else ""
    return {
        "step": "await_intent",
        "response_text": (
            f"Got it — using `{state.get('last_areapath')}`{iter_str}.\n"
            "Do you want a **status report** or to **update tasks**?"
        ),
    }


# ----------------------------------------------------------------------
# Intent routing
# ----------------------------------------------------------------------
async def handle_intent_node(state: DevState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    prompt = load_prompt("intent.md").format(user_message=state.get("user_message", ""))
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = parse_json_object(resp.content) or {}
    intent = parsed.get("intent", "unknown")

    if intent == "status":
        return await _do_status_report({**state, "intent": "status"}, config)
    if intent == "update":
        return {
            "step": "await_what_done",
            "intent": "update",
            "response_text": "Tell me what you worked on today.",
        }
    return {
        "step": "await_intent",
        "intent": "unknown",
        "response_text": parsed.get("clarify") or "Status report or update tasks?",
    }


# ----------------------------------------------------------------------
# Status report
# ----------------------------------------------------------------------
async def _do_status_report(state: DevState, config: dict) -> dict:
    cfg = config["ado"]
    items = await list_assigned(
        areapath=state["last_areapath"],
        assigned_to=state.get("user_name") or state["user_id"],
        iteration=state.get("last_iteration") or cfg.get("current_iteration_token"),
    )
    report = compute_status(items, today=date.today(), cfg=cfg)
    summary = _format_status_summary(report)
    return {
        "step": "done",
        "status_report": report,
        "response_text": summary,
    }


def _format_status_summary(r: dict) -> str:
    lines = [
        "**Status report**",
        f"- Assigned: **{r['assigned']}**",
        f"- In progress: **{r['in_progress']}**",
        f"- Overdue: **{r['overdue']}**",
        f"- Planned this week: **{r['planned_this_week']}**",
        f"- Done this week: **{r['done_this_week']}**",
        f"- Velocity (3-sprint avg): **{r['velocity_3sprint_avg']}** pts",
        f"- Sprint utilization: **{r['sprint_utilization_pct']}%**",
    ]
    actions = r.get("action_items") or []
    if actions:
        lines.append("")
        lines.append("**Action needed:**")
        for a in actions[:10]:
            lines.append(f"- {a}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Update flow
# ----------------------------------------------------------------------
async def handle_what_done_node(state: DevState, *, config: dict) -> dict:
    cfg = config["ado"]
    dev_cfg = config["dev"]
    llm = build_adapter_from_config(config)

    items = await list_assigned(
        areapath=state["last_areapath"],
        assigned_to=state.get("user_name") or state["user_id"],
        iteration=state.get("last_iteration") or cfg.get("current_iteration_token"),
    )
    if not items:
        return {
            "step": "done",
            "response_text": (
                "I couldn't find any workitems assigned to you in that areapath. "
                "Either the areapath is wrong or you have a clean queue."
            ),
        }

    compact = [
        {"id": it["id"], "title": it["title"], "state": it["state"]}
        for it in items
        if it.get("state") not in cfg.get("done_states", [])
    ]
    prompt = load_prompt("draft_updates.md").format(
        top_n=int(dev_cfg.get("candidate_top_n", 3)),
        active_state=dev_cfg.get("state_transition_on_active", "Active"),
        what_done=state.get("user_message", ""),
        assigned_json=json.dumps(compact, indent=2)[:30000],
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    candidates = parse_json_array(resp.content) or []

    # Filter by confidence threshold and clip
    candidates = [
        c for c in candidates
        if isinstance(c, dict) and c.get("workitem_id") and c.get("proposed_comment")
        and float(c.get("confidence") or 0) >= 0.5
    ][: int(dev_cfg.get("candidate_top_n", 3))]

    if not candidates:
        return {
            "step": "await_what_done",
            "response_text": (
                "I couldn't confidently match what you described to any of your assigned "
                "workitems. Could you mention a workitem id or a more specific keyword?"
            ),
        }

    summary = _format_candidates(candidates)
    return {
        "step": "await_consent",
        "what_done_text": state.get("user_message", ""),
        "candidate_updates": candidates,
        "needs_consent": True,
        "response_text": summary,
    }


def _format_candidates(cands: list[dict]) -> str:
    lines = ["I found these matches. Reply **yes** to apply all, **no** to cancel, or list ids to apply (e.g. `4521, 4602`).", ""]
    for c in cands:
        wi = c["workitem_id"]
        lines.append(f"**#{wi} — {c.get('title', '')}** (state: {c.get('state', '?')})")
        lines.append(f"  > {c['proposed_comment']}")
        if c.get("proposed_state_transition"):
            lines.append(f"  _Will also transition state -> {c['proposed_state_transition']}_")
        lines.append("")
    return "\n".join(lines)


async def handle_consent_node(state: DevState, *, config: dict) -> dict:
    dev_cfg = config["dev"]
    if not dev_cfg.get("ask_consent_before_update", True):
        # Safety: even if the config disables this, we still ask. Hard rail.
        pass

    llm = build_adapter_from_config(config)
    candidates: list[dict] = state.get("candidate_updates") or []
    ids = [int(c["workitem_id"]) for c in candidates]
    prompt = load_prompt("consent.md").format(
        ids=ids,
        user_message=state.get("user_message", ""),
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = parse_json_object(resp.content) or {}

    if parsed.get("unclear"):
        return {
            "step": "await_consent",
            "response_text": parsed.get("ask") or "Apply all, cancel, or list specific ids?",
        }

    decision = (parsed.get("apply") or "").lower()
    if decision == "none":
        return {
            "step": "done",
            "candidate_updates": [],
            "needs_consent": False,
            "response_text": "OK — cancelled, nothing was updated.",
        }
    if decision == "edit":
        instr = parsed.get("instructions") or ""
        return {
            "step": "await_what_done",
            "candidate_updates": [],
            "needs_consent": False,
            "response_text": (
                "OK — let's redraft. Tell me again what you want to update, "
                f"keeping in mind: {instr}"
            ),
        }

    if decision == "subset":
        wanted = {int(x) for x in (parsed.get("ids") or []) if str(x).lstrip("-").isdigit()}
        targets = [c for c in candidates if int(c["workitem_id"]) in wanted]
    else:
        targets = candidates

    if not targets:
        return {
            "step": "done",
            "needs_consent": False,
            "response_text": "Nothing matched the ids you provided — nothing applied.",
        }

    applied: list[dict] = []
    failures: list[dict] = []
    for c in targets:
        try:
            await update_workitem(
                int(c["workitem_id"]),
                comment=c.get("proposed_comment"),
                new_state=c.get("proposed_state_transition") or None,
            )
            applied.append({
                "workitem_id": int(c["workitem_id"]),
                "title": c.get("title"),
                "comment": c.get("proposed_comment"),
                "state_transition": c.get("proposed_state_transition"),
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("dev_apply_failed", id=c["workitem_id"])
            failures.append({"workitem_id": c["workitem_id"], "error": str(e)})

    parts = [f"Updated {len(applied)} workitem(s):"]
    for a in applied:
        parts.append(f"- #{a['workitem_id']} — {a['title']}")
    if failures:
        parts.append("")
        parts.append("Failures:")
        for f in failures:
            parts.append(f"- #{f['workitem_id']}: {f['error']}")
    return {
        "step": "done",
        "applied": applied,
        "candidate_updates": [],
        "needs_consent": False,
        "response_text": "\n".join(parts),
    }


# ----------------------------------------------------------------------
# Fallback
# ----------------------------------------------------------------------
async def handle_done_node(state: DevState, *, config: dict) -> dict:
    """Anything after a completed flow: gently restart."""
    return {
        "step": "await_intent",
        "response_text": "Anything else? You can ask for a **status report** or to **update tasks**.",
    }
