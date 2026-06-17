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
    """Keep what cross-file flow analysis needs — purpose, rules, deps, and edge cases.

    edge_cases were previously dropped; they often describe the very branches a flow takes,
    so they materially improve flow quality (the difference between a vague and a precise
    sequence diagram).
    """
    out: dict[str, dict] = {}
    for path, s in summaries.items():
        out[path] = {
            "purpose": s.get("purpose", ""),
            "business_rules": [r.get("description", "") for r in s.get("business_rules", [])][:8],
            "dependencies": s.get("dependencies", [])[:12],
            "edge_cases": (s.get("edge_cases") or [])[:5],
        }
    return out


def _compact_tree(tree_graph: dict) -> dict:
    """Trim tree-graph node attributes for the flow prompt, but KEEP the signal a flow
    tracer needs: class stereotype annotations (@RestController/@Service/@Repository) and
    method-level HTTP mappings (@GetMapping(/orders/{id})). Without these the LLM cannot
    find entry points or trace controller→service→repo flows (esp. for Java)."""
    nodes = []
    for n in tree_graph["nodes"]:
        keep = {"id": n["id"], "kind": n.get("kind"), "name": n.get("name")}
        kind = n.get("kind")
        if kind == "file":
            keep["language"] = n.get("language")
            comps = n.get("components", [])[:8]
            if comps:
                keep["components"] = comps
        elif kind == "class":
            anns = n.get("annotations") or []
            if anns:
                keep["annotations"] = anns
        elif kind == "method":
            anns = n.get("annotations") or []
            if anns:
                keep["annotations"] = anns
            if n.get("signature"):
                keep["signature"] = n["signature"]
        nodes.append(keep)
    return {"nodes": nodes, "edges": tree_graph["edges"]}


def _endpoints_block(asts: dict[str, dict]) -> list[dict]:
    """Deterministically extract endpoints from the ASTs for the flow prompt.

    cross_file runs before api_surface, so we extract endpoints here directly (same pure
    extractor api_surface uses). Giving the LLM the concrete (method, path, handler) list
    is the single biggest lever for good entry-point + flow detection in Java/Express."""
    try:
        from ..tools.treesitter_tools import extract_api_endpoints_from_asts
        endpoints, _dtos = extract_api_endpoints_from_asts(asts)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in endpoints[:60]:
        out.append({
            "method": e.get("http_method"),
            "path": e.get("path"),
            "handler": f"{e.get('handler_class') or ''}.{e.get('handler_method') or ''}".strip("."),
            "file": f"{e.get('file')}:{e.get('line')}",
            "request_body": e.get("request_body_type"),
            "auth": e.get("auth") or [],
        })
    return out


async def cross_file_node(state: CodeDocState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    summaries = state.get("file_summaries", {}) or {}
    if not summaries:
        return {"modules": [], "flows": [], "call_graph": {"edges": []}}

    languages = sorted({s.get("language", "") for s in state.get("file_inventory", [])})
    endpoints = _endpoints_block(state.get("asts") or {})
    prompt = (
        template
        .replace("{language_mix}", ", ".join(languages) or "polyglot")
        .replace("{tree_graph_json}", json.dumps(_compact_tree(state["tree_graph"]))[:120_000])
        .replace("{file_summaries_json}", json.dumps(_compact_summaries(summaries))[:120_000])
        .replace("{endpoints_json}", json.dumps(endpoints, indent=2)[:30_000] if endpoints
                 else "(no REST endpoints detected by static analysis)")
    )

    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content) or {}

    modules = parsed.get("modules", [])
    flows = parsed.get("flows", [])
    entities = parsed.get("data_entities", [])
    entry_points = parsed.get("entry_points", [])

    # Derive a simple call-graph from flow steps (Source->Target or Source-->Target).
    edges: list[list[str]] = []
    for f in flows:
        for step in f.get("steps", []):
            head, _, _ = step.partition(":")
            sep = "-->" if "-->" in head else ("->" if "->" in head else None)
            if sep:
                a, _, b = head.partition(sep)
                a, b = a.strip(), b.strip()
                if a and b:
                    edges.append([a, b])

    logger.info(
        "cross_file_done",
        modules=len(modules),
        flows=len(flows),
        entities=len(entities),
        entry_points=len(entry_points),
        endpoints_fed=len(endpoints),
    )
    if endpoints and not flows:
        logger.warning("cross_file_no_flows_despite_endpoints", endpoints=len(endpoints))

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
