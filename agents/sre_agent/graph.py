"""LangGraph definition for the SRE Agent — agentic investigator (§9.5).

One graph invocation runs ONE triage round through the investigation loop:

    Understand(intake) -> Ground(rag_search) -> Hypothesize -> Investigate(ReAct)
        -> Conclude(classify) -> {handoff_fixer | close_not_bug | ask_followup}

For interactive multi-turn, the FastAPI layer re-invokes the graph with the prior
state + the reporter's follow-up answer; hypotheses / evidence / budget survive across
rounds (intake folds the answer in and refreshes the budget). `external` and `not_a_bug`
close; `bug` (over threshold) hands off to the Fixer; everything else asks a follow-up.

The graph's outer shape and the shipped config knobs (`confidence_threshold`,
`max_followup_rounds`, `csv_max_rows`) are preserved (§9.15).
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes import (
    ask_followup_node,
    classify_node,
    close_not_bug_node,
    handoff_fixer_node,
    hypothesize_node,
    intake_node,
    investigate_node,
    rag_search_node,
)
from .nodes.ask_user import ask_user_node
from .state import SREState

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def build_graph(config: dict[str, Any] | None = None, *, checkpointer=None):
    """Compile the SRE graph. Pass a `checkpointer` to enable mid-loop ask_user
    via LangGraph interrupt()/resume (the backend does this); without one the graph
    runs straight through and questions are surfaced terminally at Conclude."""
    cfg = config or load_config()

    g = StateGraph(SREState)
    g.add_node("understand", partial(intake_node, config=cfg))
    g.add_node("ground", partial(rag_search_node, config=cfg))
    g.add_node("hypothesize", partial(hypothesize_node, config=cfg))
    g.add_node("investigate", partial(investigate_node, config=cfg))
    g.add_node("ask_user", partial(ask_user_node, config=cfg))
    g.add_node("conclude", partial(classify_node, config=cfg))
    g.add_node("handoff_fixer", partial(handoff_fixer_node, config=cfg))
    g.add_node("close_not_bug", partial(close_not_bug_node, config=cfg))
    g.add_node("ask_followup", partial(ask_followup_node, config=cfg))

    g.set_entry_point("understand")
    g.add_edge("understand", "ground")
    g.add_edge("ground", "hypothesize")
    g.add_edge("hypothesize", "investigate")

    # Investigate either pauses to ask the user (interrupt) or concludes.
    def after_investigate(state: SREState) -> str:
        return "ask_user" if state.get("pending_question") else "conclude"

    g.add_conditional_edges(
        "investigate", after_investigate,
        {"ask_user": "ask_user", "conclude": "conclude"},
    )
    g.add_edge("ask_user", "investigate")   # resume folds the answer in, loop continues

    threshold = float(cfg["sre"].get("confidence_threshold", 0.7))
    max_rounds = int(cfg["sre"].get("max_followup_rounds", 3))

    def route(state: SREState) -> str:
        v = state.get("verdict") or {}
        cls = v.get("classification", "needs_more_info")
        conf = float(v.get("confidence") or 0.0)
        rounds = int(state.get("followup_round", 0))
        if cls == "bug" and conf >= threshold:
            return "handoff_fixer"
        if cls in {"not_a_bug", "external"} and conf >= threshold:
            return "close_not_bug"
        if rounds >= max_rounds:
            # Out of follow-ups -> close best-effort rather than loop forever.
            return "close_not_bug" if cls in {"not_a_bug", "external"} else "ask_followup"
        return "ask_followup"

    g.add_conditional_edges(
        "conclude",
        route,
        {
            "handoff_fixer": "handoff_fixer",
            "close_not_bug": "close_not_bug",
            "ask_followup": "ask_followup",
        },
    )
    g.add_edge("handoff_fixer", END)
    g.add_edge("close_not_bug", END)
    g.add_edge("ask_followup", END)

    return g.compile(checkpointer=checkpointer) if checkpointer else g.compile()


# Default-compiled graph for langgraph dev (its platform supplies a checkpointer).
graph = build_graph()


# Interactive graph with a shared in-process checkpointer, used by the backend so
# mid-loop ask_user interrupts can pause and resume per conversation (thread_id).
_interactive_graph = None


def get_interactive_graph():
    global _interactive_graph
    if _interactive_graph is None:
        from langgraph.checkpoint.memory import MemorySaver
        _interactive_graph = build_graph(load_config(), checkpointer=MemorySaver())
    return _interactive_graph


# ----------------------------------------------------------------------
# Convenience entry points used by the FastAPI backend
# ----------------------------------------------------------------------
async def run_triage(
    *,
    project_id: str,
    user_message: str,
    prior_state: dict | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Run a single triage round. For multi-turn, pass the previous final state
    back in via `prior_state` and the reporter's reply as `user_message`."""
    cfg = load_config()
    g = build_graph(cfg)
    initial: SREState = dict(prior_state or {})
    initial.setdefault("project_id", project_id)
    initial["user_message"] = user_message
    if conversation_id:
        initial["conversation_id"] = conversation_id
    result = await g.ainvoke(initial)
    return dict(result)


async def triage_csv(
    *,
    project_id: str,
    rows: list[dict],
) -> list[dict]:
    """Batch triage. Each row runs the same loop under a tighter budget, no git/
    callgraph/grep tools (§9.14). Each row may contain: id, title, description,
    stack_trace, environment."""
    cfg = load_config()
    max_rows = int(cfg["sre"].get("csv_max_rows", 500))
    rows = rows[:max_rows]
    g = build_graph(cfg)
    out: list[dict] = []
    for row in rows:
        issue = {
            "title": row.get("title", "") or "",
            "description": row.get("description", "") or "",
            "stack_trace": row.get("stack_trace") or row.get("stacktrace") or "",
            "environment": row.get("environment") or row.get("env") or "",
            "repro_steps": row.get("repro_steps") or "",
            "additional_context": row.get("additional_context") or "",
        }
        state: SREState = {
            "project_id": project_id,
            "issue": issue,
            "user_message": "",
            "batch": True,
        }
        result = await g.ainvoke(state)
        verdict = result.get("verdict") or {}
        out.append(
            {
                "id": row.get("id") or row.get("ID") or "",
                "title": issue["title"],
                "verdict": verdict.get("classification"),
                "confidence": verdict.get("confidence"),
                "root_cause": verdict.get("root_cause", ""),
                "rationale": verdict.get("rationale"),
                "related_files": ";".join(verdict.get("likely_files", [])),
                "regression_commit": _regression_from(result),
                "suggested_owner": verdict.get("suggested_owner") or "",
                "next_step": verdict.get("next_step"),
            }
        )
    return out


def _regression_from(state: dict) -> str:
    import re

    for e in state.get("evidence", []) or []:
        if e.get("source") == "git":
            m = re.search(r"\b([0-9a-f]{7,40})\b", (e.get("citation", "") + " " + e.get("finding", "")))
            if m:
                return m.group(1)
    return ""
