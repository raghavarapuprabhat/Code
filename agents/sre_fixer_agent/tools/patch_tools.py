"""Apply file edits with path traversal guards.

We use full-file replacement (not unified diff) because LLMs are far more
reliable at producing whole files than at producing valid contextual diffs.
The trade-off is bigger token usage on big files; the safety win is large.
"""
from __future__ import annotations

import os
from typing import Iterable


class PatchSafetyError(RuntimeError):
    pass


def _safe_target(repo_root: str, relative_path: str) -> str:
    abs_root = os.path.abspath(repo_root)
    target = os.path.abspath(os.path.join(abs_root, relative_path))
    if not (target == abs_root or target.startswith(abs_root + os.sep)):
        raise PatchSafetyError(f"Path traversal blocked: {relative_path!r}")
    # Block writes into .git
    rel = os.path.relpath(target, abs_root)
    parts = rel.split(os.sep)
    if parts and parts[0] == ".git":
        raise PatchSafetyError(f"Refusing to write inside .git: {relative_path!r}")
    return target


def apply_edits(repo_root: str, edits: Iterable[dict]) -> list[str]:
    """Each edit: {'relative_path': str, 'new_content': str}. Returns list of touched paths."""
    touched: list[str] = []
    for edit in edits:
        rel = edit["relative_path"]
        content = edit["new_content"]
        target = _safe_target(repo_root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        touched.append(rel)
    return touched


def read_file(repo_root: str, relative_path: str) -> str:
    target = _safe_target(repo_root, relative_path)
    if not os.path.isfile(target):
        return ""
    with open(target, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()
