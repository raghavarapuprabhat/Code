"""Phase 6b — ArchSynthesis (§8.8.2): build the machine-readable Architecture Model.

Determinism first, LLM last. Edges, endpoints, datastores and layering are resolved
from the tree-graph + ASTs + ConfigInfraScan with `file:line` evidence. The LLM is used
*only* to give each detected component cluster a readable name + one-line description —
it never invents components or connectors.

The output `architecture_model` dict satisfies the contract that the SRE agent's
tools/architecture.py reads:
  component: name, layer, stereotype, files[]
  connector: from, to, kind, evidence
  endpoint:  method, path, file, request_dto
  datastore: kind, entities[], dsn_env, discovered_from
"""
from __future__ import annotations

import json
import os
import re

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "arch_synthesis.md")

# Stereotype detection from annotations / file conventions.
_STEREOTYPE_ANNOTATIONS = {
    "RestController": ("@RestController", "controller"),
    "Controller": ("@Controller", "controller"),
    "Service": ("@Service", "service"),
    "Repository": ("@Repository", "repository"),
    "Component": ("@Component", "infra"),
    "Entity": ("@Entity", "domain"),
    "Configuration": ("@Configuration", "infra"),
}
_ENDPOINT_MAPPINGS = re.compile(
    r"@(Get|Post|Put|Delete|Patch|Request)Mapping", re.IGNORECASE
)


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


def _file_nodes(tree_graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in tree_graph.get("nodes", []) if n.get("kind") == "file"}


def _path_from_file_id(file_id: str) -> str:
    return file_id.split("FILE::", 1)[-1]


def _cluster_components(tree_graph: dict, asts: dict[str, dict]) -> list[dict]:
    """Cluster files into components by package cohesion + stereotype.

    Deterministic: one cluster per top-2-level package, tagged with its dominant
    stereotype. Returns raw clusters (named later by the LLM).
    """
    by_package: dict[str, list[str]] = {}
    for rel in asts:
        pkg = os.path.dirname(rel) or "<root>"
        # Collapse to at most 2 path levels for component granularity.
        parts = pkg.split(os.sep)
        key = os.sep.join(parts[:2]) if len(parts) > 2 else pkg
        by_package.setdefault(key, []).append(rel)

    clusters: list[dict] = []
    for i, (pkg, files) in enumerate(sorted(by_package.items())):
        stereotype, layer = _dominant_stereotype(files, asts)
        class_names: list[str] = []
        for rel in files:
            for cls in (asts.get(rel, {}).get("classes") or []):
                class_names.append(cls["name"])
        clusters.append({
            "cluster_id": f"C{i}",
            "package": pkg,
            "stereotype": stereotype,
            "layer": layer,
            "files": files,
            "sample_classes": class_names[:12],
        })
    return clusters


def _dominant_stereotype(files: list[str], asts: dict[str, dict]) -> tuple[str, str]:
    """Pick the most common stereotype across a cluster's files."""
    counts: dict[str, int] = {}
    layer_of: dict[str, str] = {}
    for rel in files:
        ast = asts.get(rel, {})
        for cls in ast.get("classes", []):
            for ann in cls.get("annotations", []):
                name = (ann.get("name") or "").lstrip("@")
                if name in _STEREOTYPE_ANNOTATIONS:
                    label, layer = _STEREOTYPE_ANNOTATIONS[name]
                    counts[label] = counts.get(label, 0) + 1
                    layer_of[label] = layer
        # React components → ui.
        if ast.get("components"):
            counts["ReactComponent"] = counts.get("ReactComponent", 0) + len(ast["components"])
            layer_of["ReactComponent"] = "ui"
    if not counts:
        return "module", "unknown"
    best = max(counts, key=counts.get)
    return best, layer_of.get(best, "unknown")


def _resolve_connectors(asts: dict[str, dict], file_to_comp: dict[str, str]) -> list[dict]:
    """Component->component edges from import references, with file:line evidence."""
    # Map class name -> owning component (for resolving import targets).
    class_to_comp: dict[str, str] = {}
    for rel, ast in asts.items():
        comp = file_to_comp.get(rel)
        if not comp:
            continue
        for cls in ast.get("classes", []):
            class_to_comp[cls["name"]] = comp

    connectors: dict[tuple, dict] = {}
    for rel, ast in asts.items():
        src = file_to_comp.get(rel)
        if not src:
            continue
        for imp in ast.get("imports", []):
            # Last path segment is usually the class/symbol.
            symbol = imp.replace("/", ".").split(".")[-1]
            dst = class_to_comp.get(symbol)
            if dst and dst != src:
                key = (src, dst)
                if key not in connectors:
                    connectors[key] = {
                        "from": src, "to": dst, "kind": "call",
                        "evidence": f"{rel} (import {symbol})",
                    }
    return list(connectors.values())


def _map_endpoints(asts: dict[str, dict], file_to_comp: dict[str, str],
                   api_endpoints: list[dict]) -> list[dict]:
    """Prefer the api_surface node's detected endpoints; fall back to annotation scan."""
    endpoints: list[dict] = []
    if api_endpoints:
        for ep in api_endpoints:
            file_ref = ep.get("file", "")
            comp = file_to_comp.get(file_ref)
            endpoints.append({
                "method": ep.get("http_method", "GET"),
                "path": ep.get("path", ""),
                "file": f"{file_ref}:{ep.get('line', '')}" if file_ref else "",
                "request_dto": ep.get("request_dto"),
                "response_dto": ep.get("response_dto"),
                "auth": ", ".join(ep.get("auth") or []) or "",
                "component": comp,
            })
        return endpoints

    # Fallback: scan annotations across ASTs (method-level only available textually).
    for rel, ast in asts.items():
        for cls in ast.get("classes", []):
            for m in cls.get("methods", []):
                for ann in m.get("annotations", []):
                    if _ENDPOINT_MAPPINGS.search(ann.get("name", "")):
                        endpoints.append({
                            "method": _method_from_annotation(ann.get("name", "")),
                            "path": (ann.get("value") or ""),
                            "file": f"{rel}:{m.get('start_line', '')}",
                            "request_dto": None,
                            "response_dto": None,
                            "auth": "",
                            "component": file_to_comp.get(rel),
                        })
    return endpoints


def _method_from_annotation(ann: str) -> str:
    m = re.search(r"(Get|Post|Put|Delete|Patch)Mapping", ann)
    return m.group(1).upper() if m else "GET"


def _map_datastores(config_infra: dict, asts: dict[str, dict]) -> list[dict]:
    """Datastores from config DSNs + JPA/Prisma entity detection."""
    datastores: list[dict] = []
    # From config.
    for ds in config_infra.get("datasources", []):
        entities = _jpa_entities(asts)
        datastores.append({
            "kind": ds.get("kind", "unknown"),
            "entities": entities[:30],
            "dsn_env": ds.get("dsn_env"),
            "discovered_from": ds.get("discovered_from", "config"),
        })
    if not datastores:
        # No config DSN, but JPA entities present → infer a relational store.
        entities = _jpa_entities(asts)
        if entities:
            datastores.append({
                "kind": "relational (inferred)",
                "entities": entities[:30],
                "dsn_env": None,
                "discovered_from": "JPA",
            })
    return datastores


def _jpa_entities(asts: dict[str, dict]) -> list[str]:
    out: list[str] = []
    for ast in asts.values():
        for cls in ast.get("classes", []):
            if any((a.get("name") or "").lstrip("@") == "Entity"
                   for a in cls.get("annotations", [])):
                out.append(cls["name"])
    return out


def _detect_layers(components: list[dict], connectors: list[dict]) -> list[dict]:
    """Group components by layer; flag controller->repository edges that skip service."""
    layers: dict[str, list[str]] = {}
    layer_of = {c["name"]: c.get("layer", "unknown") for c in components}
    for c in components:
        layers.setdefault(c.get("layer", "unknown"), []).append(c["name"])

    violations: list[str] = []
    for cn in connectors:
        src_layer = layer_of.get(cn["from"], "unknown")
        dst_layer = layer_of.get(cn["to"], "unknown")
        if src_layer == "controller" and dst_layer == "repository":
            violations.append(f"{cn['from']} -> {cn['to']} (controller skips service layer; {cn['evidence']})")

    out = []
    for name, comps in layers.items():
        out.append({
            "name": name,
            "components": comps,
            "violations": [v for v in violations if any(c in v for c in comps)],
        })
    return out


async def arch_synthesis_node(state: CodeDocState, *, config: dict) -> dict:
    asts = state.get("asts") or {}
    tree_graph = state.get("tree_graph") or {"nodes": [], "edges": []}
    config_infra = state.get("config_infra") or {}
    api_endpoints = state.get("api_endpoints") or []

    if not asts:
        logger.info("arch_synthesis_skipped", reason="no asts")
        return {"architecture_model": {}}

    # 1. Cluster files into components (deterministic).
    clusters = _cluster_components(tree_graph, asts)
    file_to_comp: dict[str, str] = {}

    # 2. Name + describe components via LLM (only naming; clusters fixed).
    names = await _name_components(clusters, config)
    components: list[dict] = []
    for cl in clusters:
        named = names.get(cl["cluster_id"], {})
        comp_name = named.get("name") or _fallback_name(cl["package"])
        layer = named.get("layer") or cl["layer"]
        components.append({
            "name": comp_name,
            "layer": layer,
            "stereotype": cl["stereotype"],
            "files": cl["files"],
            "public_api": cl["sample_classes"],
            "description": named.get("description", ""),
        })
        for rel in cl["files"]:
            file_to_comp[rel] = comp_name

    # 3. Resolve connectors, endpoints, datastores, layers (deterministic).
    connectors = _resolve_connectors(asts, file_to_comp)
    endpoints = _map_endpoints(asts, file_to_comp, api_endpoints)
    datastores = _map_datastores(config_infra, asts)
    layers = _detect_layers(components, connectors)

    external_systems = [
        {
            "name": e.get("base_url_config_key", "external"),
            "kind": "http",
            "base_url_config_key": e.get("base_url_config_key"),
            "auth_style": "",
            "calling_components": [],
            "notes": f"from {e.get('discovered_from', 'config')}",
        }
        for e in config_infra.get("external_systems", [])
    ]
    deployment_units = config_infra.get("deployment_units", [])

    model = {
        "components": components,
        "connectors": connectors,
        "datastores": datastores,
        "external_systems": external_systems,
        "endpoints": endpoints,
        "deployment_units": deployment_units,
        "layers": layers,
        "decisions": [],            # filled by QualityScan/ADR inference
        "quality": {},              # filled by QualityScan
    }
    logger.info(
        "arch_synthesis_done",
        components=len(components),
        connectors=len(connectors),
        endpoints=len(endpoints),
        datastores=len(datastores),
    )
    return {"architecture_model": model}


def _fallback_name(package: str) -> str:
    stem = package.split(os.sep)[-1] if package != "<root>" else "root"
    return stem.replace("_", " ").replace("-", " ").title() or "Component"


async def _name_components(clusters: list[dict], config: dict) -> dict[str, dict]:
    """LLM names + describes clusters. Failure → empty (deterministic fallback used)."""
    if not clusters:
        return {}
    compact = [
        {
            "cluster_id": c["cluster_id"],
            "package": c["package"],
            "stereotype": c["stereotype"],
            "files": [os.path.basename(f) for f in c["files"][:8]],
            "classes": c["sample_classes"],
        }
        for c in clusters
    ]
    try:
        llm = build_adapter_from_config(config)
        prompt = _load_prompt().replace("{clusters_json}", json.dumps(compact, indent=2)[:60_000])
        resp = await llm.chat([{"role": "user", "content": prompt}])
        parsed = _safe_json(resp.content) or {}
        return {c["cluster_id"]: c for c in parsed.get("components", []) if c.get("cluster_id")}
    except Exception as exc:  # noqa: BLE001 — naming is best-effort
        logger.warning("arch_synthesis_naming_failed", err=str(exc))
        return {}


def _safe_json(text: str):
    text = (text or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
