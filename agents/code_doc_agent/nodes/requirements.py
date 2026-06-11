"""Phase 7a — RequirementsIngest + TraceLink (§8.9.1). SKIPPABLE.

If `requirements_areapath` is set in state (via the
POST /agents/code_doc/projects/{id}/requirements endpoint) AND the ADO MCP client is
reachable, this node:
  1. Ingests Epics/Features/Stories under the area path.
  2. Embeds them into the `reqs_<pid>` Chroma collection (for the chatbot).
  3. Builds a traceability matrix linking each work item to components (by keyword
     overlap with component names/files), business rules and tests — flagging
     unimplemented requirements and untraced components.

When no area path is configured or the MCP is unavailable, the node emits an empty
result and the `15_requirements_traceability` doc renders a "not configured" note.
"""
from __future__ import annotations

import re

import structlog

from ..state import CodeDocState

logger = structlog.get_logger()

_WI_TYPES = ("Epic", "Feature", "User Story", "Story", "Requirement")


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", text or "")}


def _match_components(wi_text: str, components: list[dict]) -> list[str]:
    """Link a work item to components by keyword overlap with name + file basenames."""
    wi_kw = _keywords(wi_text)
    if not wi_kw:
        return []
    matched = []
    for c in components:
        comp_kw = _keywords(c.get("name", ""))
        for f in c.get("files", [])[:20]:
            comp_kw |= _keywords(f.rsplit("/", 1)[-1])
        overlap = wi_kw & comp_kw
        if overlap:
            matched.append(c.get("name", ""))
    return matched


async def _ingest_work_items(areapath: str) -> list[dict]:
    """Pull work items from ADO. Returns [] if MCP unavailable (skippable)."""
    try:
        from shared.mcp_client.ado import ADOMCPClient
        client = ADOMCPClient()
        raw = await client.list_workitems(areapath=areapath)
    except Exception as exc:  # noqa: BLE001 — ADO not configured → skip cleanly
        logger.info("requirements_ingest_skipped", reason=str(exc)[:120])
        return []
    items = []
    for wi in raw or []:
        fields = wi.get("fields") or wi
        items.append({
            "work_item_id": str(wi.get("id") or fields.get("System.Id") or ""),
            "title": fields.get("System.Title") or wi.get("title", ""),
            "wi_type": fields.get("System.WorkItemType") or wi.get("work_item_type", ""),
            "state": fields.get("System.State") or wi.get("state", ""),
            "description": (fields.get("System.Description") or "")[:2000],
        })
    return items


def _build_matrix(items: list[dict], components: list[dict],
                  summaries: dict[str, dict]) -> dict:
    """Link work items → components → rules → tests; flag gaps.

    Each produced link is also recorded in a flat ``links`` list tagged with its
    ``method`` tier (currently the deterministic lexical matcher), so the TraceLink eval
    (§8.9.1 v0.7) can score precision/recall per tier against the labeled set.
    """
    matrix = []
    links: list[dict] = []
    traced_components: set[str] = set()
    for wi in items:
        wid = wi["work_item_id"]
        text = f"{wi.get('title','')} {wi.get('description','')}"
        comps = _match_components(text, components)
        traced_components.update(comps)
        for c in comps:
            links.append({"workitem_id": wid, "target_kind": "component",
                          "target_ref": c, "method": "lexical"})
        # Business rules whose description overlaps the work item.
        wi_kw = _keywords(text)
        rules = []
        for path, s in summaries.items():
            for r in s.get("business_rules", []):
                if _keywords(r.get("description", "")) & wi_kw:
                    ref = f"{path}: {r.get('description','')[:60]}"
                    rules.append(ref)
                    links.append({"workitem_id": wid, "target_kind": "rule",
                                  "target_ref": ref, "method": "lexical"})
        # crude test match: a test file mentioning a component keyword.
        tests = [p for p in summaries if "test" in p.lower()
                 and _keywords(p) & wi_kw][:5]
        for t in tests:
            links.append({"workitem_id": wid, "target_kind": "test",
                          "target_ref": t, "method": "lexical"})
        status = "implemented" if comps else "unimplemented"
        if comps and not rules:
            status = "partial"
        matrix.append({
            "work_item_id": wid,
            "title": wi["title"],
            "wi_type": wi["wi_type"],
            "state": wi["state"],
            "components": comps,
            "business_rules": rules[:5],
            "tests": tests,
            "status": status,
        })

    all_components = {c.get("name", "") for c in components}
    untraced = sorted(all_components - traced_components)
    return {"matrix": matrix, "untraced_components": untraced, "links": links}


def _embed_requirements(pid: str, items: list[dict]) -> None:
    if not items:
        return
    try:
        from shared.storage import ChromaStore
        store = ChromaStore()
        collection = f"reqs_{pid}"
        store.delete_collection(collection)
        ids = [f"{pid}::wi::{wi['work_item_id']}" for wi in items]
        docs = [f"{wi['wi_type']} #{wi['work_item_id']}: {wi['title']}\n{wi.get('description','')}"
                for wi in items]
        metas = [{"project_id": pid, "work_item_id": wi["work_item_id"],
                  "wi_type": wi["wi_type"], "title": wi["title"]} for wi in items]
        store.upsert(collection, ids=ids, documents=docs, metadatas=metas)
    except Exception as exc:  # noqa: BLE001
        logger.warning("requirements_embed_failed", err=str(exc))


async def _persist_trace(pid: str, matrix: dict) -> None:
    """Upsert the traceability matrix rows so the endpoint can query them."""
    import json as _json
    try:
        from sqlalchemy import text
        from shared.storage import get_session, init_db, is_sqlite, portable_sql
        if is_sqlite():
            await init_db()
        async with get_session() as session:
            for r in matrix.get("matrix", []):
                await session.execute(
                    text(portable_sql("""
                        INSERT INTO requirements_trace
                            (project_id, work_item_id, title, wi_type, state,
                             components, business_rules, tests, status)
                        VALUES (:pid, :wid, :title, :type, :state, :comps, :rules, :tests, :status)
                        ON CONFLICT (project_id, work_item_id) DO UPDATE SET
                            title=excluded.title, wi_type=excluded.wi_type, state=excluded.state,
                            components=excluded.components, business_rules=excluded.business_rules,
                            tests=excluded.tests, status=excluded.status
                    """)),
                    {
                        "pid": pid, "wid": r["work_item_id"], "title": r.get("title", ""),
                        "type": r.get("wi_type", ""), "state": r.get("state", ""),
                        "comps": _json.dumps(r.get("components", [])),
                        "rules": _json.dumps(r.get("business_rules", [])),
                        "tests": _json.dumps(r.get("tests", [])),
                        "status": r.get("status", ""),
                    },
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("requirements_persist_failed", err=str(exc))


async def requirements_node(state: CodeDocState, *, config: dict) -> dict:
    areapath = state.get("requirements_areapath")
    if not areapath:
        logger.info("requirements_skipped", reason="no areapath")
        return {"requirements": [], "traceability": {}}

    items = await _ingest_work_items(areapath)
    if not items:
        return {"requirements": [], "traceability": {}}

    components = (state.get("architecture_model") or {}).get("components", [])
    summaries = state.get("file_summaries") or {}
    matrix = _build_matrix(items, components, summaries)
    _embed_requirements(state["project_id"], items)
    await _persist_trace(state["project_id"], matrix)

    # v0.7: score the produced links per method tier against the labeled set (§8.9.1).
    trace_eval = {}
    try:
        from .trace_eval import evaluate_trace_links
        trace_eval = await evaluate_trace_links(
            project_id=state["project_id"], produced_links=matrix.get("links", []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace_eval_failed", err=str(exc))

    logger.info(
        "requirements_done",
        items=len(items),
        traced=len(matrix["matrix"]),
        untraced=len(matrix["untraced_components"]),
        trace_scored=trace_eval.get("scored"),
    )
    return {"requirements": items, "traceability": matrix, "trace_eval": trace_eval}
