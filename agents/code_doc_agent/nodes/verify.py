"""Phase 8 — coverage verification.

Enforces the "shouldn't miss a single line" requirement: every public
method/function in the AST must either appear in a business rule citation
or be explicitly marked as trivial. Missing files trigger a re-run on the
gap set up to `max_verify_loops` times.
"""
from __future__ import annotations

import structlog

from ..state import CodeDocState
from .tree_graph import methods_for_file

logger = structlog.get_logger()


async def verify_node(state: CodeDocState, *, config: dict) -> dict:
    cfg = config["code_doc"]
    max_loops = int(cfg.get("max_verify_loops", 3))
    loops = state.get("verify_loops", 0) + 1

    summaries = state.get("file_summaries") or {}
    inventory = state.get("file_inventory") or []
    tree_graph = state.get("tree_graph") or {"nodes": [], "edges": []}

    gaps: list[dict] = []
    total_methods = 0
    cited_methods = 0

    for f in inventory:
        path = f["relative_path"]
        if path not in summaries:
            gaps.append({"type": "missing_summary", "path": path})
            continue
        summary = summaries[path]
        ast_methods = set(methods_for_file(tree_graph, path))
        total_methods += len(ast_methods)
        cited = {r.get("cited_method", "") for r in summary.get("business_rules", []) if r.get("cited_method")}
        trivial = set(summary.get("trivial_methods", []))
        cited_methods += len(ast_methods & (cited | trivial))
        uncovered = ast_methods - cited - trivial
        if uncovered:
            gaps.append(
                {
                    "type": "uncovered_methods",
                    "path": path,
                    "methods": sorted(uncovered)[:30],
                    "count": len(uncovered),
                }
            )

    coverage = {
        "total_files": len(inventory),
        "summarized_files": len(summaries),
        "total_methods": total_methods,
        "cited_methods": cited_methods,
        "gaps": gaps,
        "loops_used": loops,
    }

    out: dict = {"coverage_report": coverage, "verify_loops": loops}

    if gaps and loops < max_loops:
        # Trigger another semantic pass over the gap set only.
        retry_paths = sorted({g["path"] for g in gaps})
        logger.warning("verify_gaps", gap_count=len(gaps), retry_paths=len(retry_paths))
        out["dirty_files"] = retry_paths
        out["next"] = "semantic_pass"
    else:
        if gaps:
            logger.error(
                "verify_unresolved",
                gap_count=len(gaps),
                loops=loops,
                policy="proceed_with_report",
            )
        out["next"] = "doc_gen"

    return out
