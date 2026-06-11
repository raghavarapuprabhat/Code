"""LangGraph for the ADO Developer Assistant.

A single graph processes one user turn. The conditional entry point dispatches
on `state["step"]` so the same compiled graph handles every conversational state.
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes.conversation import (
    greet_node,
    handle_areapath_node,
    handle_consent_node,
    handle_done_node,
    handle_intent_node,
    handle_what_done_node,
)
from .state import DevState

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def build_graph(config: dict[str, Any] | None = None):
    cfg = config or load_config()

    g = StateGraph(DevState)
    g.add_node("greet", partial(greet_node, config=cfg))
    g.add_node("handle_areapath", partial(handle_areapath_node, config=cfg))
    g.add_node("handle_intent", partial(handle_intent_node, config=cfg))
    g.add_node("handle_what_done", partial(handle_what_done_node, config=cfg))
    g.add_node("handle_consent", partial(handle_consent_node, config=cfg))
    g.add_node("handle_done", partial(handle_done_node, config=cfg))

    def route(state: DevState) -> str:
        step = state.get("step") or "greet"
        return {
            "greet": "greet",
            "await_areapath": "handle_areapath",
            "await_intent": "handle_intent",
            "await_what_done": "handle_what_done",
            "await_consent": "handle_consent",
            "done": "handle_done",
        }.get(step, "greet")

    g.set_conditional_entry_point(route, {
        "greet": "greet",
        "handle_areapath": "handle_areapath",
        "handle_intent": "handle_intent",
        "handle_what_done": "handle_what_done",
        "handle_consent": "handle_consent",
        "handle_done": "handle_done",
    })

    for n in ("greet", "handle_areapath", "handle_intent",
              "handle_what_done", "handle_consent", "handle_done"):
        g.add_edge(n, END)

    return g.compile()


graph = build_graph()


# ----------------------------------------------------------------------
# Public helper used by the website backend / CLI
# ----------------------------------------------------------------------
async def run_turn(*, state: dict) -> dict:
    """Process exactly one user turn. Return the new state delta."""
    cfg = load_config()
    g = build_graph(cfg)
    result = await g.ainvoke(state)
    return dict(result)
