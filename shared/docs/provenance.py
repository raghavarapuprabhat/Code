"""Requirement-content provenance markers (architecture §8.9.1 / §17, v0.7).

Second-order injection guard: requirement (ADO work-item) text is untrusted, but it flows
into generated docs and later gets retrieved by the SRE Agent as grounding. We wrap every
sentence derived from requirement text in a provenance marker:

    <req-content wi="4521">…requirement-derived text…</req-content>

The markers are:
  - **preserved** in the markdown stored in Postgres and in the Chroma chunks (so the SRE
    Agent's retrieval sees that this content is requirement-derived and treats it as data);
  - **stripped** at render time in the Documentation Hub (readers see clean prose).

This module is the single source of truth for both wrapping and stripping.
"""
from __future__ import annotations

import re

_MARKER_RE = re.compile(r"<req-content[^>]*>(.*?)</req-content>", re.DOTALL)


def mark_req_content(text: str, work_item_id: str | int) -> str:
    """Wrap requirement-derived text in a provenance marker. Idempotent-ish: callers
    pass raw requirement text, not already-marked text."""
    if not text:
        return text
    wi = str(work_item_id)
    return f'<req-content wi="{wi}">{text}</req-content>'


def strip_req_markers(markdown: str) -> str:
    """Remove the provenance wrappers, keeping the inner text. Used at Hub render time."""
    if not markdown or "<req-content" not in markdown:
        return markdown
    return _MARKER_RE.sub(lambda m: m.group(1), markdown)


def has_req_content(text: str) -> bool:
    return bool(text) and "<req-content" in text
