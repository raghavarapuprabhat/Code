"""Architecture-model tools (architecture §8.8.1, §9.7 v0.4).

Query the Code Doc Agent's machine-readable Architecture Model — components,
connectors, endpoints, datastores. The model is produced by the code-doc v0.4
ArchSynthesis node; until that lands these tools **degrade gracefully**, falling back
to the tree-graph + generated docs + a light config grep so the SRE loop still gets
discovery hints (and probe target *shapes*) today.
"""
from __future__ import annotations

import json

import structlog
from sqlalchemy import text

from shared.storage import get_session
from .rag import _project_root, get_doc

logger = structlog.get_logger()


async def _model(project_id: str) -> dict | None:
    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT model_json FROM architecture_models WHERE project_id = :p"),
                    {"p": project_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — table may not exist yet
        return None
    if not row:
        return None
    m = row.model_json
    if isinstance(m, str):
        try:
            return json.loads(m)
        except json.JSONDecodeError:
            return None
    return m


async def get_architecture(project_id: str, component: str | None = None) -> str:
    """Components, connectors, endpoints, datastores — from the Architecture Model."""
    model = await _model(project_id)
    if model:
        comps = model.get("components", [])
        if component:
            comps = [c for c in comps if component.lower() in (c.get("name", "").lower())]
        out = ["Architecture Model:"]
        for c in comps[:12]:
            out.append(f"  - {c.get('name')} [{c.get('layer', '?')}/{c.get('stereotype', '?')}] "
                       f"files={len(c.get('files', []))}")
        conns = model.get("connectors", [])[:12]
        if conns:
            out.append("Connectors:")
            for cn in conns:
                out.append(f"  - {cn.get('from')} -{cn.get('kind', 'call')}-> {cn.get('to')} "
                           f"({cn.get('evidence', '')})")
        return "\n".join(out)

    # Fallback: synthesize a coarse view from the 02_architecture doc + tree-graph packages.
    doc = await get_doc(project_id, "02_architecture")
    if doc:
        return "(no Architecture Model yet — from 02_architecture doc)\n" + doc[:1800]
    return "(no Architecture Model or 02_architecture doc available for this project)"


async def discover_endpoints(project_id: str, component: str | None = None) -> str:
    """Callable endpoints (method, path, controller file:line) for probe construction.

    Discovery fallback chain (§9.7A v0.7): (a) Architecture Model → (b) direct
    deterministic scan of the project root → (c) ask the user. The observation records
    which tier resolved (``discovered_from``) so the Evidence citation is honest.
    """
    # Tier (a): Architecture Model.
    model = await _model(project_id)
    if model and model.get("endpoints"):
        eps = model["endpoints"]
        if component:
            eps = [e for e in eps if component.lower() in json.dumps(e).lower()]
        out = ["Endpoints (discovered_from: architecture_model):"]
        for e in eps[:25]:
            out.append(f"  - {e.get('method', 'GET')} {e.get('path')}  @ {e.get('file', '?')} "
                       f"[req={e.get('request_dto', '-')}]")
        return "\n".join(out)

    # Tier (b): direct deterministic scan (no Architecture Model — reuses ConfigInfraScan
    # endpoint patterns standalone; cached per conversation).
    root = await _project_root(project_id)
    if root:
        from .direct_scan import scan_project
        scanned = scan_project(root)
        eps = scanned.get("endpoints", [])
        if component:
            eps = [e for e in eps if component.lower() in json.dumps(e).lower()]
        if eps:
            out = [
                "Endpoints (discovered_from: direct_scan):",
                "(note: project not indexed by the Code Doc Agent — a direct code scan was "
                "used. Indexing with Agent #1 would make discovery faster and richer.)",
            ]
            for e in eps[:25]:
                out.append(f"  - {e.get('method', 'GET')} {e.get('path')}  @ {e.get('file', '?')}")
            return "\n".join(out)

    # Tier (c): nothing found — caller should ask the user.
    return ("(no endpoints discovered from the Architecture Model or a direct code scan — "
            "ask the user for the endpoint/base URL to probe)")


async def discover_datasources(project_id: str) -> str:
    """Datastores + entities + DSN env-var names — for db_query target shape.

    Discovery fallback chain (§9.7A v0.7): (a) Architecture Model → (b) direct
    deterministic config scan → (c) ask the user. DSN *names* only — never values.
    """
    # Tier (a): Architecture Model.
    model = await _model(project_id)
    if model and model.get("datastores"):
        out = ["Datastores (discovered_from: architecture_model):"]
        for d in model["datastores"][:15]:
            out.append(f"  - {d.get('kind')} entities={d.get('entities', [])[:8]} "
                       f"dsn_env={d.get('dsn_env', '?')} (from {d.get('discovered_from', '?')})")
        return "\n".join(out)

    # Tier (b): direct deterministic config scan (reuses ConfigInfraScan parsers; the
    # resolved keys are env-var NAMES only, values masked at the source).
    root = await _project_root(project_id)
    if root:
        from .direct_scan import scan_project
        scanned = scan_project(root)
        ds = scanned.get("datasources", [])
        if ds:
            out = [
                "Datastores (discovered_from: direct_scan):",
                "(note: project not indexed by the Code Doc Agent — a direct config scan was "
                "used. Indexing with Agent #1 would make discovery faster and richer.)",
            ]
            for d in ds[:15]:
                out.append(f"  - {d.get('kind')} dsn_env={d.get('dsn_env', '?')} "
                           f"(from {d.get('source_file', '?')})")
            return "\n".join(out)

    # Tier (c): nothing found — caller should ask the user.
    return ("(no datastores discovered from the Architecture Model or a direct config scan — "
            "ask the user for the DSN env-var name / target to probe)")
