"""Phase 6 — cross-file analysis: modules, entry points, flows, data entities."""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "cross_file.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH, "r") as fh:
        return fh.read()


def _compact_summaries(summaries: dict[str, dict]) -> dict[str, dict]:
    """Drop verbose fields; keep only what cross-file analysis needs."""
    out: dict[str, dict] = {}
    for path, s in summaries.items():
        out[path] = {
            "purpose": s.get("purpose", ""),
            "business_rules": [r.get("description", "") for r in s.get("business_rules", [])][:6],
            "dependencies": s.get("dependencies", [])[:10],
        }
    return out


def _compact_tree(tree_graph: dict) -> dict:
    """Trim tree-graph node attributes for cross-file prompt."""
    nodes = []
    for n in tree_graph["nodes"]:
        keep = {"id": n["id"], "kind": n.get("kind"), "name": n.get("name")}
        if n.get("kind") == "file":
            keep["language"] = n.get("language")
            keep["components"] = n.get("components", [])[:8]
        nodes.append(keep)
    return {"nodes": nodes, "edges": tree_graph["edges"]}


async def cross_file_node(state: CodeDocState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    summaries = state.get("file_summaries", {}) or {}
    if not summaries:
        return {"modules": [], "flows": [], "call_graph": {"edges": []}}

    languages = sorted({s.get("language", "") for s in state.get("file_inventory", [])})
    prompt = (
        template
        .replace("{language_mix}", ", ".join(languages) or "polyglot")
        .replace("{tree_graph_json}", json.dumps(_compact_tree(state["tree_graph"]))[:120_000])
        .replace("{file_summaries_json}", json.dumps(_compact_summaries(summaries))[:120_000])
    )

    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content) or {}

    modules = parsed.get("modules", [])
    flows = parsed.get("flows", [])
    entities = parsed.get("data_entities", [])
    entry_points = parsed.get("entry_points", [])

    # Derive a simple call-graph from flows (entry -> involved file/module).
    edges: list[list[str]] = []
    for f in flows:
        ep = f.get("entry_point", "")
        for step in f.get("steps", []):
            if "->" in step:
                head, _, _ = step.partition(":")
                a, _, b = head.partition("->")
                edges.append([a.strip(), b.strip()])

    logger.info(
        "cross_file_done",
        modules=len(modules),
        flows=len(flows),
        entities=len(entities),
    )

    return {
        "modules": modules,
        "flows": flows,
        "call_graph": {"edges": edges, "entry_points": entry_points},
        "data_entities": entities,
    }


def _safe_json(text: str):
    import json
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
