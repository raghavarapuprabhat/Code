"""Documentation Hub data access (v0.2).

Reads generated documentation from Postgres (``generated_docs``) — the source of
truth populated by the code_doc agent's persist node. Confluence storage-format
HTML is rendered on demand from the stored markdown; nothing is read from disk.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import text

from shared.docs import markdown_to_confluence_html, markdown_to_html, strip_req_markers
from shared.storage import get_session, iso_ts

DocFormat = Literal["markdown", "html", "confluence"]


async def project_exists(project_id: str) -> bool:
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT 1 FROM code_projects WHERE id = :id"),
                {"id": project_id},
            )
        ).first()
    return row is not None


async def list_docs(project_id: str) -> list[dict]:
    """Return ordered document metadata for a project (drives the doc tree)."""
    async with get_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT doc_id, title, audience, sort_order, generated_at "
                    "FROM generated_docs WHERE project_id = :pid "
                    "ORDER BY sort_order, doc_id"
                ),
                {"pid": project_id},
            )
        ).all()
    return [
        {
            "doc_id": r.doc_id,
            "title": r.title,
            "audience": r.audience,
            "sort_order": r.sort_order,
            "generated_at": iso_ts(r.generated_at),
        }
        for r in rows
    ]


async def get_doc(project_id: str, doc_id: str, fmt: DocFormat = "markdown") -> dict | None:
    """Fetch one document's content in the requested format, or None if absent.

    ``markdown`` returns the stored source; ``html`` renders vanilla HTML;
    ``confluence`` renders Confluence storage-format HTML (mermaid macros).
    """
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT doc_id, title, audience, content_md, generated_at "
                    "FROM generated_docs WHERE project_id = :pid AND doc_id = :did"
                ),
                {"pid": project_id, "did": doc_id},
            )
        ).first()
    if not row:
        return None

    # v0.7: strip <req-content> provenance markers at render time (§8.9.1). They are
    # preserved in the stored markdown + Chroma chunks so the SRE Agent sees requirement
    # provenance; readers get clean prose.
    content_md = strip_req_markers(row.content_md)
    if fmt == "confluence":
        content = markdown_to_confluence_html(content_md)
    elif fmt == "html":
        content = markdown_to_html(content_md)
    else:
        content = content_md

    return {
        "doc_id": row.doc_id,
        "title": row.title,
        "audience": row.audience,
        "format": fmt,
        "content": content,
        "generated_at": iso_ts(row.generated_at),
    }
