"""Shared documentation helpers used by both the code_doc agent and the API.

Single source of truth for markdown -> Confluence-HTML conversion and the
canonical document metadata (doc_id -> title/audience/order).
"""
from .provenance import has_req_content, mark_req_content, strip_req_markers
from .render import (
    DOC_METADATA,
    doc_metadata,
    markdown_to_confluence_html,
    markdown_to_html,
)

__all__ = [
    "DOC_METADATA",
    "doc_metadata",
    "markdown_to_confluence_html",
    "markdown_to_html",
    "mark_req_content",
    "strip_req_markers",
    "has_req_content",
]
