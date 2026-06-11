"""RAG + code-access helpers for the SRE investigator.

``search_code_docs`` queries **both** Chroma collections the Code Doc Agent
populates — ``docs_<pid>`` (flows, business logic, sequence diagrams) and
``code_<pid>`` (per-file summaries) — and merges them, mirroring the chatbot
retriever (§13A.5, §9.7). ``fetch_code_snippet`` and ``get_business_rules``
already existed; the agentic loop finally wires them in. ``get_doc`` pulls a
full generated document for the affected area.
"""
from __future__ import annotations

import os
from typing import Any

import structlog
from sqlalchemy import text

from shared.storage import ChromaStore, get_session

logger = structlog.get_logger()


def _query_collection(
    store: ChromaStore, collection: str, query: str, n_results: int
) -> list[dict[str, Any]]:
    try:
        res = store.query(
            collection_name=collection, query_texts=[query], n_results=n_results
        )
    except Exception as e:  # noqa: BLE001 — Chroma offline / empty collection
        logger.warning("rag_query_failed", collection=collection, err=str(e))
        return []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    out: list[dict[str, Any]] = []
    kind = "docs" if collection.startswith("docs_") else "code"
    for doc, meta, dist in zip(docs, metas, dists):
        meta = meta or {}
        if kind == "docs":
            ref = f"doc:{meta.get('doc_id', '?')}"
            if meta.get("heading_path"):
                ref += f"#{meta['heading_path']}"
        else:
            ref = meta.get("relative_path", "")
        out.append(
            {
                "relative_path": ref,
                "score": float(1.0 - (dist or 0.0)),
                "snippet": (doc or "")[:1200],
                "collection": kind,
                "metadata": meta,
            }
        )
    return out


def search_code_docs(
    project_id: str,
    query: str,
    *,
    n_results: int = 6,
) -> list[dict[str, Any]]:
    """Return up to ~n hits merged from ``docs_<pid>`` and ``code_<pid>``.

    Each collection contributes its top results; the merged list is sorted by
    score and de-duped by reference so the grounding step sees both what the
    system is *supposed* to do (docs) and how a file is summarized (code).
    """
    try:
        store = ChromaStore()
    except Exception as e:  # noqa: BLE001 — Chroma server unreachable
        logger.warning("rag_store_unavailable", err=str(e))
        return []
    per = max(n_results, 3)
    hits = _query_collection(store, f"docs_{project_id}", query, per) + _query_collection(
        store, f"code_{project_id}", query, per
    )
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for h in sorted(hits, key=lambda x: x["score"], reverse=True):
        key = h["relative_path"] or h["snippet"][:60]
        if key in seen:
            continue
        seen.add(key)
        merged.append(h)
    return merged[: n_results + 2]


async def _project_root(project_id: str) -> str | None:
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT project_path FROM code_projects WHERE id = :id"),
                {"id": project_id},
            )
        ).first()
    return row.project_path if row else None


def _resolve_in_root(root: str, relative_path: str) -> str | None:
    """Resolve ``relative_path`` under ``root``, rejecting path traversal (§17)."""
    abs_root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(abs_root, relative_path))
    if target != abs_root and not target.startswith(abs_root + os.sep):
        return None
    return target


async def fetch_code_snippet(
    project_id: str,
    relative_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """Read a slice of a file from disk, guarded by the project root.

    Accepts a bare basename (e.g. from a Java stack frame) and resolves it to the
    first matching file under the project root.
    """
    root = await _project_root(project_id)
    if not root:
        return ""
    target = _resolve_in_root(root, relative_path)
    if not target or not os.path.isfile(target):
        # Stack frames often carry only a basename — find it under the root.
        target = _find_by_basename(root, os.path.basename(relative_path))
    if not target:
        return ""
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    s = max(start_line - 1, 0)
    e = min(end_line, len(lines))
    rel = os.path.relpath(target, os.path.abspath(root))
    body = "".join(lines[s:e])
    return f"{rel}:{start_line}-{end_line}\n{body}"


def _find_by_basename(root: str, basename: str) -> str | None:
    abs_root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "target", "dist", ".venv"}]
        if basename in filenames:
            return os.path.join(dirpath, basename)
    return None


async def get_business_rules(project_id: str, relative_path: str) -> list[dict]:
    """Return the persisted business rules for a single file (Agent #1 output)."""
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
        # Try a basename match — stack frames rarely carry the full relative path.
        base = os.path.basename(relative_path)
        async with get_session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT summary_json FROM code_file_summaries "
                        "WHERE project_id = :p AND relative_path LIKE :r LIMIT 1"
                    ),
                    {"p": project_id, "r": f"%{base}"},
                )
            ).first()
    if not row:
        return []
    summary = row.summary_json
    if isinstance(summary, str):
        import json
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            return []
    return (summary or {}).get("business_rules", [])


async def get_doc(project_id: str, doc_id: str) -> str:
    """Pull a full generated document (e.g. ``04_flows``) from ``generated_docs``."""
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT content_md FROM generated_docs "
                    "WHERE project_id = :p AND doc_id = :d"
                ),
                {"p": project_id, "d": doc_id},
            )
        ).first()
    return row.content_md if row else ""
