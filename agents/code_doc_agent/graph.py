"""LangGraph definition for the Code Documentation Agent (v0.5 topology, §8.9.0).

Node order (full index):
    ingest -> ast_extract -> tree_graph -> config_infra -> incremental_check ->
    semantic_pass -> cross_file -> api_surface -> batch_jobs -> verify ->
    {semantic_pass | arch_synthesis} ->
    arch_synthesis -> quality_scan -> arch_persist ->
    requirements -> test_trace -> db_drift -> dependency_audit ->
    doc_gen -> doc_critique -> doc_eval -> drift_digest -> persist -> END

v0.4 added: config_infra, arch_synthesis, quality_scan, arch_persist, doc_critique.
v0.5 added: requirements, test_trace, db_drift, dependency_audit, doc_eval, drift_digest.
All v0.4/v0.5 nodes are graceful: missing inputs (no model, no ADO, no auditor) produce
empty/"not configured" results rather than failing the run.
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes.api_surface import api_surface_node
from .nodes.arch_persist import arch_persist_node
from .nodes.arch_synthesis import arch_synthesis_node
from .nodes.ast_extract import ast_extract_node
from .nodes.batch_jobs import batch_jobs_node
from .nodes.config_infra import config_infra_node
from .nodes.cross_file import cross_file_node
from .nodes.doc_critique import doc_critique_node
from .nodes.doc_eval import doc_eval_node
from .nodes.doc_gen import doc_gen_node
from .nodes.drift_digest import drift_digest_node
from .nodes.incremental import incremental_check_node
from .nodes.ingest import ingest_node
from .nodes.persist import persist_node
from .nodes.quality_scan import quality_scan_node
from .nodes.requirements import requirements_node
from .nodes.semantic_pass import semantic_pass_node
from .nodes.tree_graph import tree_graph_node
from .nodes.v05_extras import db_drift_node, dependency_audit_node, test_trace_node
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
    g.add_node("config_infra", partial(config_infra_node, config=cfg))
    g.add_node("incremental_check", partial(incremental_check_node, config=cfg))
    g.add_node("semantic_pass", partial(semantic_pass_node, config=cfg))
    g.add_node("cross_file", partial(cross_file_node, config=cfg))
    g.add_node("api_surface", partial(api_surface_node, config=cfg))
    g.add_node("batch_jobs", partial(batch_jobs_node, config=cfg))
    g.add_node("verify", partial(verify_node, config=cfg))
    # v0.4 architecture reconstruction
    g.add_node("arch_synthesis", partial(arch_synthesis_node, config=cfg))
    g.add_node("quality_scan", partial(quality_scan_node, config=cfg))
    g.add_node("arch_persist", partial(arch_persist_node, config=cfg))
    # v0.5 skippable enrichment
    g.add_node("requirements", partial(requirements_node, config=cfg))
    g.add_node("test_trace", partial(test_trace_node, config=cfg))
    g.add_node("db_drift", partial(db_drift_node, config=cfg))
    g.add_node("dependency_audit", partial(dependency_audit_node, config=cfg))
    # doc generation + quality + evals
    g.add_node("doc_gen", partial(doc_gen_node, config=cfg))
    g.add_node("doc_critique", partial(doc_critique_node, config=cfg))
    g.add_node("doc_eval", partial(doc_eval_node, config=cfg))
    g.add_node("drift_digest", partial(drift_digest_node, config=cfg))
    g.add_node("persist", partial(persist_node, config=cfg))

    g.set_entry_point("ingest")
    g.add_edge("ingest", "ast_extract")
    g.add_edge("ast_extract", "tree_graph")
    g.add_edge("tree_graph", "config_infra")            # v0.4: deterministic infra scan
    g.add_edge("config_infra", "incremental_check")

    def post_incremental(state: CodeDocState) -> str:
        if not state.get("dirty_files"):
            # No changes -> straight to doc_gen (re-render from cached summaries/model).
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
        # verify sets next="semantic_pass" to re-loop, or "doc_gen" when satisfied.
        # In v0.4+ the satisfied path enters architecture synthesis first.
        return "semantic_pass" if state.get("next") == "semantic_pass" else "arch_synthesis"

    g.add_conditional_edges("verify", post_verify, {
        "semantic_pass": "semantic_pass",
        "arch_synthesis": "arch_synthesis",
    })

    # v0.4 architecture reconstruction chain.
    g.add_edge("arch_synthesis", "quality_scan")
    g.add_edge("quality_scan", "arch_persist")
    # v0.5 skippable enrichment chain (each no-ops when not configured).
    g.add_edge("arch_persist", "requirements")
    g.add_edge("requirements", "test_trace")
    g.add_edge("test_trace", "db_drift")
    g.add_edge("db_drift", "dependency_audit")
    g.add_edge("dependency_audit", "doc_gen")

    # Doc generation -> quality gate -> evals -> drift digest -> persist.
    g.add_edge("doc_gen", "doc_critique")
    g.add_edge("doc_critique", "doc_eval")
    g.add_edge("doc_eval", "drift_digest")
    g.add_edge("drift_digest", "persist")
    g.add_edge("persist", END)

    return g.compile()


# Export a default-compiled graph for `langgraph dev`.
graph = build_graph()


async def run_indexing(
    *,
    project_path: str,
    mode: str = "full",
    display_name: str | None = None,
    requirements_areapath: str | None = None,
) -> dict:
    """Public helper used by the FastAPI backend and the standalone CLI."""
    cfg = load_config()
    g = build_graph(cfg)
    initial: CodeDocState = {
        "project_path": project_path,
        "mode": mode,  # type: ignore[typeddict-item]
        "display_name": display_name,
    }
    if requirements_areapath:
        initial["requirements_areapath"] = requirements_areapath
    final_state = await g.ainvoke(initial)
    model = final_state.get("architecture_model") or {}
    return {
        "project_id": final_state.get("project_id"),
        "files_indexed": len(final_state.get("file_inventory", [])),
        "summaries": len(final_state.get("file_summaries") or {}),
        "coverage": final_state.get("coverage_report"),
        "docs_generated": sorted((final_state.get("generated_docs") or {}).keys()),
        # v0.4/v0.5 surface
        "architecture_components": len(model.get("components", [])),
        "model_hash": final_state.get("model_hash", ""),
        "eval_score": (final_state.get("eval_results") or {}).get("score"),
        "requirements_traced": len((final_state.get("traceability") or {}).get("matrix", [])),
    }
