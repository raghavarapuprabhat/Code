"""LangGraph definition for the Code Documentation Agent.

Node order:
    ingest -> ast_extract -> tree_graph -> incremental_check ->
    semantic_pass -> cross_file -> api_surface -> batch_jobs -> verify ->
    {semantic_pass | doc_gen} -> persist -> END
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes.api_surface import api_surface_node
from .nodes.ast_extract import ast_extract_node
from .nodes.batch_jobs import batch_jobs_node
from .nodes.cross_file import cross_file_node
from .nodes.doc_gen import doc_gen_node
from .nodes.incremental import incremental_check_node
from .nodes.ingest import ingest_node
from .nodes.persist import persist_node
from .nodes.semantic_pass import semantic_pass_node
from .nodes.tree_graph import tree_graph_node
from .nodes.verify import verify_node
from .state import CodeDocState

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    p = path or CONFIG_PATH
    with open(p) as fh:
        return yaml.safe_load(fh)


def build_graph(config: dict[str, Any] | None = None):
    cfg = config or load_config()

    g = StateGraph(CodeDocState)

    g.add_node("ingest", partial(ingest_node, config=cfg))
    g.add_node("ast_extract", partial(ast_extract_node, config=cfg))
    g.add_node("tree_graph", partial(tree_graph_node, config=cfg))
    g.add_node("incremental_check", partial(incremental_check_node, config=cfg))
    g.add_node("semantic_pass", partial(semantic_pass_node, config=cfg))
    g.add_node("cross_file", partial(cross_file_node, config=cfg))
    g.add_node("api_surface", partial(api_surface_node, config=cfg))
    g.add_node("batch_jobs", partial(batch_jobs_node, config=cfg))
    g.add_node("verify", partial(verify_node, config=cfg))
    g.add_node("doc_gen", partial(doc_gen_node, config=cfg))
    g.add_node("persist", partial(persist_node, config=cfg))

    g.set_entry_point("ingest")
    g.add_edge("ingest", "ast_extract")
    g.add_edge("ast_extract", "tree_graph")
    g.add_edge("tree_graph", "incremental_check")

    def post_incremental(state: CodeDocState) -> str:
        if not state.get("dirty_files"):
            # No changes detected in incremental mode -> jump straight to doc_gen
            # (which still re-renders from cached summaries).
            return "doc_gen"
        return "semantic_pass"

    g.add_conditional_edges("incremental_check", post_incremental, {
        "semantic_pass": "semantic_pass",
        "doc_gen": "doc_gen",
    })
    g.add_edge("semantic_pass", "cross_file")
    g.add_edge("cross_file", "api_surface")
    g.add_edge("api_surface", "batch_jobs")
    g.add_edge("batch_jobs", "verify")

    def post_verify(state: CodeDocState) -> str:
        return state.get("next") or "doc_gen"

    g.add_conditional_edges("verify", post_verify, {
        "semantic_pass": "semantic_pass",
        "doc_gen": "doc_gen",
    })
    g.add_edge("doc_gen", "persist")
    g.add_edge("persist", END)

    return g.compile()


# Export a default-compiled graph for `langgraph dev`.
graph = build_graph()


async def run_indexing(*, project_path: str, mode: str = "full", display_name: str | None = None) -> dict:
    """Public helper used by the FastAPI backend and the standalone CLI."""
    cfg = load_config()
    g = build_graph(cfg)
    initial: CodeDocState = {
        "project_path": project_path,
        "mode": mode,  # type: ignore[typeddict-item]
        "display_name": display_name,
    }
    final_state = await g.ainvoke(initial)
    return {
        "project_id": final_state.get("project_id"),
        "files_indexed": len(final_state.get("file_inventory", [])),
        "summaries": len(final_state.get("file_summaries") or {}),
        "coverage": final_state.get("coverage_report"),
        "docs_generated": sorted((final_state.get("generated_docs") or {}).keys()),
    }
