"""Architecture-model tools (architecture §8.8.1, §9.7 v0.4).

Query the Code Doc Agent's machine-readable Architecture Model — components,
connectors, endpoints, datastores. The model is produced by the code-doc v0.4
ArchSynthesis node; until that lands these tools **degrade gracefully**, falling back
to the tree-graph + generated docs + a light config grep so the SRE loop still gets
discovery hints (and probe target *shapes*) today.
"""
from __future__ import annotations

import json
import re

import structlog
from sqlalchemy import text

from shared.storage import get_session
from .codebase import grep_code
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
    """Callable endpoints (method, path, controller file:line) for probe construction."""
    model = await _model(project_id)
    if model and model.get("endpoints"):
        eps = model["endpoints"]
        if component:
            eps = [e for e in eps if component.lower() in json.dumps(e).lower()]
        out = ["Endpoints (from Architecture Model):"]
        for e in eps[:25]:
            out.append(f"  - {e.get('method', 'GET')} {e.get('path')}  @ {e.get('file', '?')} "
                       f"[req={e.get('request_dto', '-')}]")
        return "\n".join(out)

    # Fallback: grep controller annotations / route definitions + 07_api_surface doc.
    hits = []
    for pat in (
        r'@(?:Get|Post|Put|Delete|Request)Mapping',
        r'@RestController',
        r'\.(?:get|post|put|delete)\(["\']/',     # express/axios/fetch route literals
        r'@app\.(?:route|get|post)',
    ):
        res = await grep_code(project_id, pat, max_results=15)
        if res:
            hits.append(res)
    doc = await get_doc(project_id, "07_api_surface")
    parts = ["(no Architecture Model yet — endpoints inferred from code grep)"]
    if hits:
        parts.append("\n".join(hits)[:1600])
    if doc:
        parts.append("From 07_api_surface:\n" + doc[:1200])
    return "\n".join(parts) if (hits or doc) else "(no endpoints discovered)"


async def discover_datasources(project_id: str) -> str:
    """Datastores + entities + DSN env-var names — for db_query target shape."""
    model = await _model(project_id)
    if model and model.get("datastores"):
        out = ["Datastores (from Architecture Model):"]
        for d in model["datastores"][:15]:
            out.append(f"  - {d.get('kind')} entities={d.get('entities', [])[:8]} "
                       f"dsn_env={d.get('dsn_env', '?')} (from {d.get('discovered_from', '?')})")
        return "\n".join(out)

    # Fallback: grep config for datasource URLs + the 03_data_model doc.
    root = await _project_root(project_id)
    parts = ["(no Architecture Model yet — datasources inferred from config grep)"]
    if root:
        cfg = await grep_code(
            project_id,
            r'(?:spring\.datasource\.url|DATABASE_URL|jdbc:|mongodb(?:\+srv)?:|DATASOURCE|_DB_URL|_DSN)',
            max_results=20,
        )
        if cfg:
            # Surface the *keys*, never the secret values.
            masked = re.sub(r'(=|:)\s*\S+', r'\1 «value»', cfg)
            parts.append(masked[:1400])
    doc = await get_doc(project_id, "03_data_model")
    if doc:
        parts.append("From 03_data_model:\n" + doc[:1000])
    return "\n".join(parts)
