"""Code-structure tools for the investigator — call graph, flows, grep.

These read Agent #1's persisted artifacts (the NetworkX tree-graph and the
``04_flows`` document) plus the project source on disk. All file access is
confined to the project root (§17).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog
from sqlalchemy import text

from shared.storage import get_session
from .rag import _project_root, get_doc

logger = structlog.get_logger()

_SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", ".venv", "__pycache__", ".idea"}
_SOURCE_EXT = {".java", ".js", ".jsx", ".ts", ".tsx", ".py", ".xml", ".yml", ".yaml", ".properties"}


async def _tree_graph(project_id: str) -> dict[str, Any] | None:
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT graph_json FROM code_tree_graphs WHERE project_id = :p"),
                {"p": project_id},
            )
        ).first()
    if not row:
        return None
    g = row.graph_json
    if isinstance(g, str):
        try:
            return json.loads(g)
        except json.JSONDecodeError:
            return None
    return g


async def get_call_graph(project_id: str, symbol: str) -> str:
    """Structural neighborhood of a method/function from the tree-graph + references.

    The persisted tree-graph is a containment graph (file → class → method), not a
    resolved call graph, so this returns the symbol's location and its declaring
    file/class, then greps the repo for textual references as candidate callers —
    honest about being reference-based, which is what a dev eyeballs first.
    """
    g = await _tree_graph(project_id)
    if not g:
        return f"(no tree-graph indexed for this project — cannot locate {symbol})"

    needle = symbol.split(".")[-1].split("(")[0].strip()
    matches = []
    for n in g.get("nodes", []):
        nid = n.get("id", "")
        if not (nid.startswith("M::") or nid.startswith("FN::")):
            continue
        if n.get("name") == needle or needle in nid:
            parts = nid.split("::")
            rel = parts[1] if len(parts) > 1 else "?"
            owner = parts[2] if nid.startswith("M::") and len(parts) > 2 else ""
            matches.append(
                {
                    "rel": rel,
                    "owner": owner,
                    "name": n.get("name"),
                    "signature": n.get("signature"),
                    "start": n.get("start"),
                    "end": n.get("end"),
                }
            )
    out: list[str] = []
    if matches:
        out.append(f"Declarations of '{needle}':")
        for m in matches[:6]:
            loc = f"{m['rel']}:{m['start']}-{m['end']}" if m.get("start") else m["rel"]
            owner = f"{m['owner']}." if m["owner"] else ""
            out.append(f"  - {owner}{m['name']}{m.get('signature') or ''} @ {loc}")
    else:
        out.append(f"No declaration of '{needle}' found in the tree-graph.")

    refs = await grep_code(project_id, rf"\b{re.escape(needle)}\b", max_results=15)
    out.append("\nTextual references (candidate callers / call sites):")
    out.append(refs or "  (none found)")
    return "\n".join(out)


async def get_flow(project_id: str, entry_point: str) -> str:
    """Return the traced flow for an entry point from the ``04_flows`` document."""
    doc = await get_doc(project_id, "04_flows")
    if not doc:
        return "(no 04_flows document generated for this project)"
    needle = entry_point.lower().strip()
    # Extract the heading section that mentions the entry point.
    sections = re.split(r"(?m)^(#{1,6}\s+.*)$", doc)
    # re.split with a capturing group yields [pre, heading, body, heading, body, ...]
    best = ""
    for i in range(1, len(sections) - 1, 2):
        heading, body = sections[i], sections[i + 1]
        if needle and (needle in heading.lower() or needle in body.lower()[:400]):
            best = f"{heading}\n{body}".strip()
            break
    if best:
        return best[:2500]
    return doc[:2500]


async def grep_code(project_id: str, pattern: str, *, max_results: int = 30) -> str:
    """Literal/regex search across the project source for a symbol or string."""
    root = await _project_root(project_id)
    if not root:
        return ""
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    abs_root = os.path.abspath(root)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in _SOURCE_EXT:
                continue
            fpath = os.path.join(dirpath, fn)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if rx.search(line):
                            rel = os.path.relpath(fpath, abs_root)
                            out.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                            if len(out) >= max_results:
                                return "\n".join(out)
            except OSError:
                continue
    return "\n".join(out)
