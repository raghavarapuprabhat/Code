"""Phase 3 — build the project tree-graph and persist it.

This is the token-saving artifact: project -> packages -> files -> classes -> methods.
The LLM downstream sees this compact JSON instead of the raw codebase.
"""
from __future__ import annotations

import json
import os
from typing import Any

import networkx as nx
import structlog
from sqlalchemy import text

from shared.storage import get_session, portable_sql
from ..state import CodeDocState

logger = structlog.get_logger()


def _ann_names(annotations: list[dict] | None) -> list[str]:
    """Just the annotation names — for class stereotypes (@Service, @Entity…)."""
    return [a.get("name", "") for a in (annotations or []) if a.get("name")]


def _ann_pairs(annotations: list[dict] | None) -> list[str]:
    """Annotation name + value (e.g. 'GetMapping(/orders/{id})') — for method-level
    HTTP mappings the flow-tracer turns into endpoint steps. Kept compact for token cost."""
    out: list[str] = []
    for a in annotations or []:
        name = a.get("name")
        if not name:
            continue
        val = a.get("value")
        out.append(f"{name}({val})" if val else name)
    return out


def build_tree_graph(asts: dict[str, dict]) -> dict:
    g = nx.DiGraph()
    g.add_node("PROJECT", kind="project")
    for rel_path, ast in asts.items():
        package = os.path.dirname(rel_path) or "<root>"
        pkg_id = f"PKG::{package}"
        if not g.has_node(pkg_id):
            g.add_node(pkg_id, kind="package", name=package)
            g.add_edge("PROJECT", pkg_id)
        file_id = f"FILE::{rel_path}"
        g.add_node(
            file_id,
            kind="file",
            name=os.path.basename(rel_path),
            language=ast.get("language"),
            imports=ast.get("imports", []),
            components=ast.get("components", []),
            hooks=ast.get("hooks", []),
        )
        g.add_edge(pkg_id, file_id)
        for cls in ast.get("classes", []):
            cls_id = f"CLS::{rel_path}::{cls['name']}"
            g.add_node(
                cls_id,
                kind="class",
                name=cls["name"],
                start=cls["start_line"],
                end=cls["end_line"],
                # Class stereotype annotations (@RestController/@Service/@Repository/@Entity…)
                # are the signal the flow-tracer needs to find entry points + layers.
                annotations=_ann_names(cls.get("annotations")),
            )
            g.add_edge(file_id, cls_id)
            for m in cls.get("methods", []):
                mid = f"M::{rel_path}::{cls['name']}::{m['name']}"
                g.add_node(mid, kind="method", name=m["name"], signature=m.get("signature"),
                           start=m["start_line"], end=m["end_line"],
                           # @GetMapping("/orders/{id}") etc. — endpoint + route on the method.
                           annotations=_ann_pairs(m.get("annotations")))
                g.add_edge(cls_id, mid)
        for fn in ast.get("functions", []):
            fid = f"FN::{rel_path}::{fn['name']}"
            g.add_node(fid, kind="function", name=fn["name"], signature=fn.get("signature"),
                       start=fn["start_line"], end=fn["end_line"])
            g.add_edge(file_id, fid)
    return _serialize(g)


def _serialize(g: nx.DiGraph) -> dict[str, Any]:
    return {
        "nodes": [{"id": n, **g.nodes[n]} for n in g.nodes],
        "edges": [[u, v] for u, v in g.edges],
    }


def methods_for_file(tree_graph: dict, relative_path: str) -> list[str]:
    """Coverage helper: list every method/function for a given file."""
    out: list[str] = []
    for n in tree_graph["nodes"]:
        nid = n["id"]
        if not (nid.startswith("M::") or nid.startswith("FN::")):
            continue
        # M::<path>::<class>::<method>  or  FN::<path>::<fn>
        parts = nid.split("::")
        if len(parts) >= 3 and parts[1] == relative_path:
            if parts[0] == "M":
                out.append(f"{parts[2]}.{parts[3]}")
            else:
                out.append(parts[2])
    return out


async def tree_graph_node(state: CodeDocState, *, config: dict) -> dict:
    g = build_tree_graph(state["asts"])
    pid = state["project_id"]
    async with get_session() as session:
        await session.execute(
            text(
                portable_sql(
                    """
                INSERT INTO code_tree_graphs (project_id, graph_json)
                VALUES (:id, CAST(:g AS JSONB))
                ON CONFLICT (project_id) DO UPDATE
                SET graph_json = EXCLUDED.graph_json, updated_at = now()
                """
                )
            ),
            {"id": pid, "g": json.dumps(g)},
        )
        await session.commit()
    logger.info(
        "tree_graph_done",
        project_id=pid,
        nodes=len(g["nodes"]),
        edges=len(g["edges"]),
    )
    return {"tree_graph": g}
