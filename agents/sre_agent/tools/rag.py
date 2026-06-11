"""RAG helpers — query the Code Doc Agent's Chroma collection for a project."""
from __future__ import annotations

import os
from typing import Any

import structlog
from sqlalchemy import text

from shared.storage import ChromaStore, get_session

logger = structlog.get_logger()


def search_code_docs(
    project_id: str,
    query: str,
    *,
    n_results: int = 6,
) -> list[dict[str, Any]]:
    """Returns up to n hits from the Code Doc Agent's collection."""
    store = ChromaStore()
    try:
        res = store.query(
            collection_name=f"code_{project_id}",
            query_texts=[query],
            n_results=n_results,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("rag_query_failed", project_id=project_id, err=str(e))
        return []

    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    hits: list[dict[str, Any]] = []
    for doc, meta, dist in zip(docs, metas, dists):
        hits.append(
            {
                "relative_path": (meta or {}).get("relative_path", ""),
                "score": float(1.0 - (dist or 0.0)),
                "snippet": doc[:1200],
                "metadata": meta or {},
            }
        )
    return hits


async def fetch_code_snippet(
    project_id: str,
    relative_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """Read a slice of a file from disk, guarded by the project root."""
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT project_path FROM code_projects WHERE id = :id"),
                {"id": project_id},
            )
        ).first()
    if not row:
        return ""
    root = row.project_path
    abs_root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(abs_root, relative_path))
    if not target.startswith(abs_root + os.sep) and target != abs_root:
        return ""
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    s = max(start_line - 1, 0)
    e = min(end_line, len(lines))
    return "".join(lines[s:e])


async def get_business_rules(project_id: str, relative_path: str) -> list[dict]:
    """Return the persisted business rules for a single file."""
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT summary_json FROM code_file_summaries "
                    "WHERE project_id = :p AND relative_path = :r"
                ),
                {"p": project_id, "r": relative_path},
            )
        ).first()
    if not row:
        return []
    summary = row.summary_json or {}
    return summary.get("business_rules", [])
