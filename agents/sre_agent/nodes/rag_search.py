"""Phase 2 — query Chroma for documentation snippets relevant to the issue."""
from __future__ import annotations

import structlog

from ..state import SREState
from ..tools.rag import search_code_docs

logger = structlog.get_logger()


async def rag_search_node(state: SREState, *, config: dict) -> dict:
    cfg = config["sre"]
    project_id = state.get("project_id")
    if not project_id:
        logger.warning("rag_search_no_project")
        return {"rag_hits": []}

    issue = state.get("issue") or {}
    query_parts = [
        issue.get("title", ""),
        issue.get("description", ""),
        issue.get("stack_trace", "") or "",
    ]
    # Include the latest user message for follow-up rounds.
    if state.get("user_message"):
        query_parts.append(state["user_message"])
    query = "\n".join(p for p in query_parts if p).strip()
    if not query:
        return {"rag_hits": []}

    hits = search_code_docs(project_id, query, n_results=int(cfg.get("rag_top_k", 6)))
    logger.info("rag_search_done", project_id=project_id, hits=len(hits))
    return {"rag_hits": hits}
