"""Markdown rendering + Confluence storage-format conversion + doc metadata.

Factored out of the code_doc agent's doc_gen node (v0.2) so the FastAPI backend
can render stored markdown to Confluence HTML on demand, and so the canonical
doc_id -> {title, audience, sort_order} mapping lives in one place.
"""
from __future__ import annotations

import re

from markdown_it import MarkdownIt

# Canonical metadata for the documents the code_doc agent generates, keyed by
# doc_id (the historical filename stem without extension). `audience` groups
# documents in the Documentation Hub sidebar.
DOC_METADATA: dict[str, dict] = {
    "01_management_overview": {"title": "Management Overview", "audience": "management", "sort_order": 1},
    "02_architecture": {"title": "Architecture", "audience": "architecture", "sort_order": 2},
    "03_data_model": {"title": "Data Model", "audience": "developer", "sort_order": 3},
    "04_flows": {"title": "Flows", "audience": "developer", "sort_order": 4},
    "05_sequence_diagrams": {"title": "Sequence Diagrams", "audience": "developer", "sort_order": 5},
    "06_business_logic": {"title": "Business Logic", "audience": "developer", "sort_order": 6},
    "07_api_surface": {"title": "API Surface", "audience": "developer", "sort_order": 7},
    "08_batch_jobs": {"title": "Batch Jobs & Scheduled Tasks", "audience": "developer", "sort_order": 8},
}


def doc_metadata(doc_id: str) -> dict:
    """Return {title, audience, sort_order} for a doc_id, with sensible fallbacks.

    Unknown doc_ids (e.g. future 09_*) get a humanized title and high sort_order
    so they appear after the known set without needing a code change.
    """
    if doc_id in DOC_METADATA:
        return dict(DOC_METADATA[doc_id])
    stem = re.sub(r"^\d+_", "", doc_id)
    title = stem.replace("_", " ").strip().title() or doc_id
    return {"title": title, "audience": "developer", "sort_order": 999}


def markdown_to_html(markdown: str) -> str:
    """Render markdown to vanilla HTML (CommonMark + soft breaks, HTML passthrough)."""
    md = MarkdownIt("commonmark", {"breaks": True, "html": True})
    return md.render(markdown)


def markdown_to_confluence_html(markdown: str) -> str:
    """Render markdown to Confluence storage-format-compatible HTML.

    Mermaid code blocks are wrapped in the 'mermaid-cloud' Confluence macro;
    all other HTML passes through unchanged.
    """
    return _to_confluence_html(markdown_to_html(markdown))


def _to_confluence_html(html: str) -> str:
    def repl(match: re.Match) -> str:
        code = match.group(1)
        return (
            '<ac:structured-macro ac:name="mermaid-cloud">'
            f"<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )

    return re.sub(
        r'<pre><code class="language-mermaid">([\s\S]*?)</code></pre>',
        repl,
        html,
    )
