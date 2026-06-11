"""LangGraph for the ADO MD Agent.

Two graphs:
- etl_graph   : list_squads -> fetch_workitems -> compute_metrics
                -> detect_raid -> generate_achievements -> persist_snapshots
- drill_graph : load_snapshot -> maybe_live_query -> synthesize_answer
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes.drill import (
    load_snapshot_node,
    maybe_live_query_node,
    synthesize_answer_node,
)
from .nodes.etl_achievements import generate_achievements_node
from .nodes.etl_fetch import fetch_workitems_node, list_squads_node
from .nodes.etl_metrics import compute_metrics_node
from .nodes.etl_persist import persist_snapshots_node
from .nodes.etl_raid import detect_raid_node
from .state import DrillState, ETLState

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def build_etl_graph(config: dict[str, Any] | None = None):
    cfg = config or load_config()
    g = StateGraph(ETLState)
    g.add_node("list_squads", partial(list_squads_node, config=cfg))
    g.add_node("fetch_workitems", partial(fetch_workitems_node, config=cfg))
    g.add_node("compute_metrics", partial(compute_metrics_node, config=cfg))
    g.add_node("detect_raid", partial(detect_raid_node, config=cfg))
    g.add_node("generate_achievements", partial(generate_achievements_node, config=cfg))
    g.add_node("persist_snapshots", partial(persist_snapshots_node, config=cfg))

    g.set_entry_point("list_squads")
    g.add_edge("list_squads", "fetch_workitems")
    g.add_edge("fetch_workitems", "compute_metrics")
    g.add_edge("compute_metrics", "detect_raid")
    g.add_edge("detect_raid", "generate_achievements")
    g.add_edge("generate_achievements", "persist_snapshots")
    g.add_edge("persist_snapshots", END)
    return g.compile()


def build_drill_graph(config: dict[str, Any] | None = None):
    cfg = config or load_config()
    g = StateGraph(DrillState)
    g.add_node("load_snapshot", partial(load_snapshot_node, config=cfg))
    g.add_node("maybe_live_query", partial(maybe_live_query_node, config=cfg))
    g.add_node("synthesize_answer", partial(synthesize_answer_node, config=cfg))

    g.set_entry_point("load_snapshot")
    g.add_edge("load_snapshot", "maybe_live_query")
    g.add_edge("maybe_live_query", "synthesize_answer")
    g.add_edge("synthesize_answer", END)
    return g.compile()


# Default-compiled graphs for `langgraph dev`.
etl_graph = build_etl_graph()
drill_graph = build_drill_graph()


# ----------------------------------------------------------------------
# Public helpers used by the website backend / CLI
# ----------------------------------------------------------------------
async def run_etl(snapshot_date: str | None = None) -> dict:
    cfg = load_config()
    g = build_etl_graph(cfg)
    result = await g.ainvoke({"snapshot_date": snapshot_date} if snapshot_date else {})
    return {
        "snapshot_date": result.get("snapshot_date"),
        "persisted": result.get("persisted"),
        "errors": result.get("errors") or [],
    }


async def run_drill(*, question: str, squad_filter: str | None = None, snapshot_date: str | None = None) -> dict:
    cfg = load_config()
    g = build_drill_graph(cfg)
    result = await g.ainvoke(
        {
            "user_question": question,
            "squad_filter": squad_filter,
            "snapshot_date": snapshot_date,
        }
    )
    return {
        "answer": result.get("answer"),
        "citations": result.get("citations") or [],
        "snapshot_date": result.get("snapshot_date"),
        "used_live": bool((result.get("live_extra") or {}).get("workitem_count")),
    }
