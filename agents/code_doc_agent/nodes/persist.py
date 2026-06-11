"""Phase 9 — persist generated docs (v0.2).

Stores the generated documentation in two places:
  * Postgres ``generated_docs`` — full markdown per doc_id (source of truth for rendering)
  * Chroma ``docs_<pid>`` — heading-aware chunks for the project chatbot's RAG

Per-file code summaries continue to be embedded into ``code_<pid>`` so the SRE
agent and the chatbot can also retrieve file-level detail. No files are written
to disk (the .docs/ markdown + confluence output was removed in v0.2).
"""
from __future__ import annotations

import hashlib

import structlog
from sqlalchemy import text

from shared.docs import doc_metadata
from shared.storage import ChromaStore, get_session, init_db, is_sqlite, portable_sql
from ..state import CodeDocState

logger = structlog.get_logger()

# Heading-aware chunking targets (~800-1000 tokens). We approximate tokens as
# words * 1.3; keep it dependency-free for the POC.
_MAX_CHUNK_WORDS = 700
_OVERLAP_WORDS = 80

# Postgres runtime safety-net DDL so a standalone agent works against a fresh
# Postgres without the seed file. On SQLite the schema comes from init_db()
# (shared.storage.schema), so this PG-typed DDL is skipped there.
_PG_DDL = """
CREATE TABLE IF NOT EXISTS generated_docs (
    project_id    TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
    doc_id        TEXT NOT NULL,
    title         TEXT NOT NULL,
    audience      TEXT,
    sort_order    INT NOT NULL DEFAULT 0,
    content_md    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, doc_id)
)
"""

_UPSERT = """
INSERT INTO generated_docs
    (project_id, doc_id, title, audience, sort_order, content_md, content_hash, generated_at)
VALUES
    (:pid, :doc_id, :title, :audience, :sort_order, :content_md, :content_hash, now())
ON CONFLICT (project_id, doc_id) DO UPDATE SET
    title = EXCLUDED.title,
    audience = EXCLUDED.audience,
    sort_order = EXCLUDED.sort_order,
    content_md = EXCLUDED.content_md,
    content_hash = EXCLUDED.content_hash,
    generated_at = now()
"""


async def persist_node(state: CodeDocState, *, config: dict) -> dict:
    pid = state["project_id"]
    generated_docs: dict[str, str] = state.get("generated_docs") or {}
    summaries = state.get("file_summaries") or {}

    store = ChromaStore()

    # 1. Persist generated documents to the DB + embed into docs_<pid>.
    if generated_docs:
        # Ensure tables exist: SQLite via init_db(), Postgres via runtime DDL.
        if is_sqlite():
            await init_db()
        async with get_session() as session:
            if not is_sqlite():
                await session.execute(text(_PG_DDL))
            for doc_id, content_md in generated_docs.items():
                meta = doc_metadata(doc_id)
                content_hash = hashlib.sha256(content_md.encode("utf-8")).hexdigest()
                await session.execute(
                    text(portable_sql(_UPSERT)),
                    {
                        "pid": pid,
                        "doc_id": doc_id,
                        "title": meta["title"],
                        "audience": meta["audience"],
                        "sort_order": meta["sort_order"],
                        "content_md": content_md,
                        "content_hash": content_hash,
                    },
                )
            await session.commit()

        _embed_docs(store, pid, generated_docs)

    # 2. Embed per-file code summaries into code_<pid> (unchanged behavior).
    if summaries:
        _embed_summaries(store, pid, summaries)

    # 3. Mark the project indexed.
    async with get_session() as session:
        await session.execute(
            text(portable_sql("UPDATE code_projects SET last_indexed = now() WHERE id = :id")),
            {"id": pid},
        )
        await session.commit()

    logger.info(
        "persist_done",
        project_id=pid,
        docs=len(generated_docs),
        summaries=len(summaries),
    )
    return {}


def _embed_docs(store: ChromaStore, pid: str, generated_docs: dict[str, str]) -> None:
    """Chunk each document heading-aware and upsert into Chroma docs_<pid>.

    The collection is reset first so chunks removed/renamed since the last run
    don't linger; stable IDs keep a single run idempotent.
    """
    collection = f"docs_{pid}"
    store.delete_collection(collection)

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    for doc_id, content_md in generated_docs.items():
        meta = doc_metadata(doc_id)
        for i, (heading_path, chunk) in enumerate(_chunk_markdown(content_md)):
            ids.append(f"{pid}::{doc_id}::{i}")
            docs.append(chunk)
            metas.append(
                {
                    "project_id": pid,
                    "doc_id": doc_id,
                    "title": meta["title"],
                    "audience": meta["audience"],
                    "heading_path": heading_path,
                    "chunk_index": i,
                }
            )
    if ids:
        store.upsert(collection, ids=ids, documents=docs, metadatas=metas)


def _embed_summaries(store: ChromaStore, pid: str, summaries: dict[str, dict]) -> None:
    collection = f"code_{pid}"
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    for path, s in summaries.items():
        ids.append(f"{pid}::{path}")
        docs.append(_render_summary_doc(path, s))
        metas.append(
            {
                "project_id": pid,
                "relative_path": path,
                "purpose": s.get("purpose", ""),
            }
        )
    store.upsert(collection, ids=ids, documents=docs, metadatas=metas)


def _chunk_markdown(content_md: str) -> list[tuple[str, str]]:
    """Split markdown into ~700-word chunks, tracking the current heading path.

    Returns a list of (heading_path, chunk_text). Splits on headings; if a single
    section exceeds the size budget it is further split with word overlap.
    """
    lines = content_md.splitlines()
    sections: list[tuple[str, list[str]]] = []
    heading_stack: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current and any(ln.strip() for ln in current):
            sections.append((" › ".join(heading_stack), current.copy()))
        current.clear()

    for ln in lines:
        if ln.startswith("#"):
            flush()
            level = len(ln) - len(ln.lstrip("#"))
            title = ln[level:].strip()
            heading_stack[:] = heading_stack[: max(level - 1, 0)]
            heading_stack.append(title)
        else:
            current.append(ln)
    flush()

    chunks: list[tuple[str, str]] = []
    for heading_path, body_lines in sections:
        text_block = "\n".join(body_lines).strip()
        if not text_block:
            continue
        words = text_block.split()
        if len(words) <= _MAX_CHUNK_WORDS:
            chunks.append((heading_path, _with_heading(heading_path, text_block)))
            continue
        start = 0
        while start < len(words):
            window = words[start : start + _MAX_CHUNK_WORDS]
            chunks.append((heading_path, _with_heading(heading_path, " ".join(window))))
            if start + _MAX_CHUNK_WORDS >= len(words):
                break
            start += _MAX_CHUNK_WORDS - _OVERLAP_WORDS

    # Whole-document fallback (e.g. a doc with no headings and a short body).
    if not chunks and content_md.strip():
        chunks.append(("", content_md.strip()))
    return chunks


def _with_heading(heading_path: str, body: str) -> str:
    return f"{heading_path}\n\n{body}" if heading_path else body


def _render_summary_doc(path: str, summary: dict) -> str:
    lines = [f"# {path}", "", f"Purpose: {summary.get('purpose', '')}", ""]
    rules = summary.get("business_rules", [])
    if rules:
        lines.append("Business rules:")
        for r in rules:
            cl = r.get("cited_lines", [0, 0])
            lines.append(
                f"- {r.get('description', '')} "
                f"({r.get('cited_file', path)}:{cl[0]}-{cl[1]})"
            )
    deps = summary.get("dependencies", [])
    if deps:
        lines.append("")
        lines.append("Dependencies: " + ", ".join(deps[:20]))
    edges = summary.get("edge_cases", [])
    if edges:
        lines.append("")
        lines.append("Edge cases:")
        for e in edges:
            lines.append(f"- {e}")
    return "\n".join(lines)
