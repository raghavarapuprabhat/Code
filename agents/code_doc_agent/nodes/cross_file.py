"""Phase 6 — cross-file analysis: modules, entry points, flows, data entities."""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState
from ..tools.json_tools import extract_json
from ..tools.mermaid_tools import parse_flow_step

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


# ----------------------------------------------------------------------
# Deterministic seeds (computed from ASTs/tree-graph, independent of the LLM).
# These guarantee non-empty Data Model / Call Graph / Flows even when the LLM
# underperforms on a large repo. The LLM enriches; it does not originate.
# ----------------------------------------------------------------------

_ENTITY_CLASS_ANNOTATIONS = {"Entity", "Document", "Table"}
_SUPERCLASS_ANNOTATIONS = {"MappedSuperclass", "Entity"}
_RELATION_CARDINALITY = {
    "OneToMany": "||--o{",
    "ManyToOne": "}o--||",
    "ManyToMany": "}o--o{",
    "OneToOne": "||--||",
}


def _inner_type(type_str: str) -> str:
    """Set<Visit> -> Visit, List<Pet> -> Pet, Pet -> Pet, Pet[] -> Pet."""
    t = (type_str or "").strip()
    if "<" in t and ">" in t:
        t = t[t.find("<") + 1 : t.rfind(">")]
    return t.replace("[]", "").split(",")[-1].strip()


def _superclass_name(cls: dict) -> str | None:
    """Best-effort `extends` target. tree-sitter doesn't capture superclass as a field
    here, so we recover it from the class signature text when present, else None."""
    return cls.get("superclass") or None


def _entities_from_asts(asts: dict[str, dict]) -> list[dict]:
    """Build ER entities from @Entity/@Document classes, flattening @MappedSuperclass
    parents so inherited PKs/columns (e.g. BaseEntity.id) appear, and deriving relations
    from JPA association annotations on fields."""
    # Index every class (entity or superclass) by name for inheritance resolution.
    by_name: dict[str, dict] = {}
    for ast in asts.values():
        if ast.get("language") != "java":
            continue
        for cls in ast.get("classes", []):
            by_name[cls.get("name", "")] = cls

    def _is_entity(cls: dict) -> bool:
        names = {(a.get("name") or "").lstrip("@") for a in cls.get("annotations", [])}
        return bool(names & _ENTITY_CLASS_ANNOTATIONS)

    def _flatten_fields(cls: dict, seen: set[str]) -> list[dict]:
        """Own fields plus fields inherited from a (mapped) superclass, parent-first."""
        name = cls.get("name", "")
        if name in seen:
            return []
        seen.add(name)
        fields: list[dict] = []
        parent = _superclass_name(cls)
        if parent and parent in by_name:
            fields.extend(_flatten_fields(by_name[parent], seen))
        fields.extend(cls.get("fields", []))
        return fields

    entities: list[dict] = []
    for cls in by_name.values():
        if not _is_entity(cls):
            continue
        flat = _flatten_fields(cls, set())
        fields: list[dict] = []
        relations: list[dict] = []
        for f in flat:
            anns = {(a.get("name") or "").lstrip("@") for a in f.get("annotations", [])}
            rel = anns & set(_RELATION_CARDINALITY)
            if rel:
                kind = next(iter(rel))
                relations.append({
                    "target": _inner_type(f.get("type", "")),
                    "cardinality": _RELATION_CARDINALITY[kind],
                    "label": f.get("name", kind),
                })
            else:
                fields.append({"name": f.get("name", ""), "type": f.get("type", "Object")})
        entities.append({
            "name": cls.get("name", ""),
            "fields": fields,
            "relations": relations,
        })
    return entities


def _merge_entities(deterministic: list[dict], llm: list[dict]) -> list[dict]:
    """Union by name; deterministic fields/relations win (grounded in the AST). The LLM
    may contribute entities the static scan missed (e.g. NoSQL schemas it inferred)."""
    out = {e["name"]: e for e in deterministic if e.get("name")}
    for e in llm or []:
        name = e.get("name")
        if name and name not in out:
            out[name] = e
    return list(out.values())


def _class_to_file(asts: dict[str, dict]) -> dict[str, str]:
    m: dict[str, str] = {}
    for rel, ast in asts.items():
        for cls in ast.get("classes", []):
            m[cls.get("name", "")] = rel
    return m


def _call_graph_from_tree(asts: dict[str, dict], endpoints: list[dict]) -> dict:
    """Deterministic class->class edges from imports + field types resolved to classes
    known in this repo. Entry points come from the detected REST endpoints."""
    class_files = _class_to_file(asts)
    known = set(class_files)
    edges: set[tuple[str, str]] = set()
    for rel, ast in asts.items():
        for cls in ast.get("classes", []):
            src = cls.get("name", "")
            if not src:
                continue
            targets: set[str] = set()
            # Field types (DI/composition) — strongest signal for layer wiring.
            for f in cls.get("fields", []):
                t = _inner_type(f.get("type", ""))
                if t in known and t != src:
                    targets.add(t)
            # Imports resolved to repo classes.
            for imp in ast.get("imports", []):
                sym = imp.replace("/", ".").split(".")[-1]
                if sym in known and sym != src:
                    targets.add(sym)
            for dst in targets:
                edges.add((src, dst))
    entry_points = [
        f"{e.get('handler')} ({e.get('method')} {e.get('path')})"
        for e in endpoints
    ]
    return {"edges": [[a, b] for a, b in sorted(edges)], "entry_points": entry_points}


def _baseline_flows(endpoints: list[dict], asts: dict[str, dict]) -> list[dict]:
    """One flow per endpoint, traced Client->Controller->Service->Repository->back using
    deterministic field-type wiring. Steps use the canonical `Source->Target: msg` form
    (and `-->` for the return) the renderers expect."""
    class_files = _class_to_file(asts)
    # class -> collaborator classes via field types (already the layer wiring we want).
    collaborators: dict[str, list[str]] = {}
    for ast in asts.values():
        for cls in ast.get("classes", []):
            deps = []
            for f in cls.get("fields", []):
                t = _inner_type(f.get("type", ""))
                if t in class_files and t != cls.get("name"):
                    deps.append(t)
            collaborators[cls.get("name", "")] = deps

    flows: list[dict] = []
    for e in endpoints:
        handler = e.get("handler", "")
        controller = handler.split(".")[0] if "." in handler else handler
        method = (e.get("method") or "GET")
        path = e.get("path") or "/"
        steps = [f"Client->{controller}: {method} {path}"]
        # Follow one hop into each collaborator (service), then its collaborators (repo).
        seen = {controller}
        for svc in collaborators.get(controller, []):
            if svc in seen:
                continue
            seen.add(svc)
            steps.append(f"{controller}->{svc}: {handler.split('.')[-1] if '.' in handler else 'handle'}()")
            for repo in collaborators.get(svc, []):
                if repo in seen:
                    continue
                seen.add(repo)
                steps.append(f"{svc}->{repo}: query/persist()")
                steps.append(f"{repo}-->{svc}: result")
            steps.append(f"{svc}-->{controller}: result")
        steps.append(f"{controller}-->Client: response")
        flows.append({
            "name": f"{method} {path}",
            "entry_point": f"{e.get('file', '')} ({method} {path})",
            "trigger": f"Client calls {method} {path}",
            "steps": steps,
        })
    return flows


def _dedup_edges(*edge_lists: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, str]] = set()
    out: list[list[str]] = []
    for edges in edge_lists:
        for a, b in edges:
            if (a, b) not in seen:
                seen.add((a, b))
                out.append([a, b])
    return out


def _merge_flows(llm_flows: list[dict], baseline_flows: list[dict]) -> list[dict]:
    """Prefer the richer LLM flow per endpoint; fall back to the deterministic baseline
    for endpoints the LLM didn't cover, so Flows + Sequence Diagrams are never empty."""
    covered = {(f.get("name") or "").strip() for f in llm_flows}
    out = list(llm_flows)
    for bf in baseline_flows:
        if (bf.get("name") or "").strip() not in covered:
            out.append(bf)
    return out


# Cross-file synthesis emits many flows + entities + business rules at once. The
# default 4k cap truncates the JSON on real repos (finish_reason=length), which then
# fails to parse. Give this call enough room; the model still stops early when done.
_CROSS_FILE_MAX_TOKENS = 16_000


async def _chat_json(llm, prompt: str) -> dict:
    """Call the LLM and parse JSON, with one retry that re-states the JSON-only contract.
    Uses provider-native JSON mode where supported (OpenAI/DeepSeek/custom) and a raised
    max_tokens so the (large) synthesis JSON isn't truncated. Returns {} on total
    failure — callers fall back to deterministic seeds."""
    json_mode = getattr(llm, "supports_json_mode", lambda: False)()
    max_tokens = max(_CROSS_FILE_MAX_TOKENS, getattr(getattr(llm, "cfg", None), "max_tokens", 0) or 0)
    try:
        resp = await llm.chat(
            [{"role": "user", "content": prompt}], json_mode=json_mode, max_tokens=max_tokens
        )
        parsed = _safe_json(resp.content)
        if parsed is not None:
            return parsed
        logger.warning("cross_file_json_parse_failed_retrying",
                       finish_reason=_finish_reason(resp))
        retry = await llm.chat(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": "That was not valid JSON. Return ONLY the JSON object, no prose, no code fences."},
            ],
            json_mode=json_mode,
            max_tokens=max_tokens,
        )
        parsed = _safe_json(retry.content)
        if parsed is not None:
            return parsed
        logger.warning("cross_file_json_parse_failed_final",
                       finish_reason=_finish_reason(retry))
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort; seeds carry the docs
        logger.warning("cross_file_llm_failed", err=str(exc))
    return {}


def _finish_reason(resp) -> str:
    """Best-effort extraction of the provider finish_reason for diagnostics."""
    try:
        return resp.raw.choices[0].finish_reason or ""
    except Exception:  # noqa: BLE001
        return ""


async def cross_file_node(state: CodeDocState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    summaries = state.get("file_summaries", {}) or {}
    if not summaries:
        # No per-file summaries (e.g. nothing dirty), but the ASTs still carry enough
        # to populate the data model / call graph / flows deterministically.
        asts = state.get("asts") or {}
        endpoints = _endpoints_block(asts)
        det_cg = _call_graph_from_tree(asts, endpoints)
        return {
            "modules": [],
            "flows": _baseline_flows(endpoints, asts),
            "call_graph": det_cg,
            "data_entities": _entities_from_asts(asts),
            "business_logic": [],
        }

    asts = state.get("asts") or {}
    languages = sorted({s.get("language", "") for s in state.get("file_inventory", [])})
    endpoints = _endpoints_block(asts)

    # --- Deterministic seeds (independent of the LLM). These are the floor: the doc
    #     sections are never empty when this data exists, regardless of LLM behavior. ---
    det_entities = _entities_from_asts(asts)
    det_call_graph = _call_graph_from_tree(asts, endpoints)
    det_flows = _baseline_flows(endpoints, asts)

    prompt = (
        template
        .replace("{language_mix}", ", ".join(languages) or "polyglot")
        .replace("{tree_graph_json}", json.dumps(_compact_tree(state["tree_graph"]))[:120_000])
        .replace("{file_summaries_json}", json.dumps(_compact_summaries(summaries))[:120_000])
        .replace("{endpoints_json}", json.dumps(endpoints, indent=2)[:30_000] if endpoints
                 else "(no REST endpoints detected by static analysis)")
    )

    # LLM enriches the deterministic seeds. One retry on unparseable output; on total
    # failure we proceed with the deterministic seeds rather than emptying the docs.
    parsed = await _chat_json(llm, prompt)

    modules = parsed.get("modules", [])
    llm_flows = parsed.get("flows", []) or []
    llm_entities = parsed.get("data_entities", []) or []
    llm_entry_points = parsed.get("entry_points", []) or []
    business_logic = parsed.get("business_logic", []) or []

    # Edges derived from LLM flow steps (using the shared, arrow-correct parser).
    llm_edges: list[list[str]] = []
    for f in llm_flows:
        for step in f.get("steps", []):
            p = parse_flow_step(step)
            if p:
                a, b, _msg, _ret = p
                llm_edges.append([a, b])

    # --- Merge: deterministic is the base, LLM adds richness, neither zeroes the other. ---
    entities = _merge_entities(det_entities, llm_entities)
    flows = _merge_flows(llm_flows, det_flows)
    edges = _dedup_edges(llm_edges, det_call_graph["edges"])
    entry_points = llm_entry_points or det_call_graph["entry_points"]

    logger.info(
        "cross_file_done",
        modules=len(modules),
        flows=len(flows),
        llm_flows=len(llm_flows),
        det_flows=len(det_flows),
        entities=len(entities),
        det_entities=len(det_entities),
        edges=len(edges),
        business_logic=len(business_logic),
        endpoints_fed=len(endpoints),
    )

    return {
        "modules": modules,
        "flows": flows,
        "call_graph": {"edges": edges, "entry_points": entry_points},
        "data_entities": entities,
        "business_logic": business_logic,
    }


def _safe_json(text: str):
    return extract_json(text)
