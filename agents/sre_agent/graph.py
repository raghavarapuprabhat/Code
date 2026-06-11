"""LangGraph definition for the SRE Agent.

A single graph invocation runs ONE triage round:
   intake -> rag_search -> classify -> {handoff_fixer | close_not_bug | ask_followup}

For interactive mode, the FastAPI router runs the graph repeatedly: the user's
follow-up answer is appended to user_message and the graph re-runs, accumulating
classification_history until confidence crosses the threshold or max rounds hit.
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes.classify import classify_node
from .nodes.decide import ask_followup_node, close_not_bug_node, handoff_fixer_node
from .nodes.intake import intake_node
from .nodes.rag_search import rag_search_node
from .state import SREState

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def build_graph(config: dict[str, Any] | None = None):
    cfg = config or load_config()

    g = StateGraph(SREState)
    g.add_node("intake", partial(intake_node, config=cfg))
    g.add_node("rag_search", partial(rag_search_node, config=cfg))
    g.add_node("classify", partial(classify_node, config=cfg))
    g.add_node("handoff_fixer", partial(handoff_fixer_node, config=cfg))
    g.add_node("close_not_bug", partial(close_not_bug_node, config=cfg))
    g.add_node("ask_followup", partial(ask_followup_node, config=cfg))

    g.set_entry_point("intake")
    g.add_edge("intake", "rag_search")
    g.add_edge("rag_search", "classify")

    threshold = float(cfg["sre"].get("confidence_threshold", 0.7))
    max_rounds = int(cfg["sre"].get("max_followup_rounds", 3))

    def route(state: SREState) -> str:
        v = state.get("verdict") or {}
        cls = v.get("classification", "needs_more_info")
        conf = float(v.get("confidence") or 0.0)
        rounds = int(state.get("followup_round", 0))
        if cls == "bug" and conf >= threshold:
            return "handoff_fixer"
        if cls == "not_a_bug" and conf >= threshold:
            return "close_not_bug"
        if rounds >= max_rounds:
            # Out of follow-ups -> default to needs_more_info closure: surface verdict as-is.
            return "close_not_bug" if cls == "not_a_bug" else "ask_followup"
        return "ask_followup"

    g.add_conditional_edges(
        "classify",
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

    return g.compile()


# Default-compiled graph for langgraph dev.
graph = build_graph()


# ----------------------------------------------------------------------
# Convenience entry points used by the FastAPI backend
# ----------------------------------------------------------------------
async def run_triage(
    *,
    project_id: str,
    user_message: str,
    prior_state: dict | None = None,
) -> dict:
    """Run a single triage round. For multi-turn, pass the previous final state
    back in via `prior_state` and append the user's reply to `user_message`."""
    cfg = load_config()
    g = build_graph(cfg)
    initial: SREState = dict(prior_state or {})
    initial.setdefault("project_id", project_id)
    initial["user_message"] = user_message
    result = await g.ainvoke(initial)
    return dict(result)


async def triage_csv(
    *,
    project_id: str,
    rows: list[dict],
) -> list[dict]:
    """Batch triage. Each row may contain: id, title, description, stack_trace, environment."""
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
        }
        result = await g.ainvoke(state)
        verdict = result.get("verdict") or {}
        out.append(
            {
                "id": row.get("id") or row.get("ID") or "",
                "title": issue["title"],
                "verdict": verdict.get("classification"),
                "confidence": verdict.get("confidence"),
                "rationale": verdict.get("rationale"),
                "likely_files": ";".join(verdict.get("likely_files", [])),
                "suggested_owner": verdict.get("suggested_owner") or "",
                "next_step": verdict.get("next_step"),
            }
        )
    return out
