"""Filesystem helpers: walk, hash, classify."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from typing import Iterable

LANG_BY_EXT = {
    ".java": "java",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
}


def project_id_for(path: str) -> str:
    return hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ignored(rel_path: str, patterns: Iterable[str]) -> bool:
    norm = rel_path.replace(os.sep, "/")
    segments = norm.split("/")
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat):
            return True
        # support glob ** vs simple fnmatch limitation
        if pat.endswith("/**") and norm.startswith(pat[:-3]):
            return True
        # `**/<dir>/**` — prune/ignore any path that contains <dir> as a segment.
        # Lets us also prune the directory itself (e.g. "com/x/test") during the walk,
        # not just the files inside it.
        if pat.startswith("**/") and pat.endswith("/**"):
            middle = pat[3:-3]
            if "/" not in middle and "*" not in middle and middle in segments:
                return True
    return False


def walk_project(
    root: str,
    *,
    languages: list[str],
    ignore_patterns: list[str],
) -> list[dict]:
    out: list[dict] = []
    accepted_exts = {ext for ext, lang in LANG_BY_EXT.items() if lang in languages}
    for dirpath, dirnames, filenames in os.walk(root):
        # prune ignored directories early for speed
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(os.path.join(rel_dir, d), ignore_patterns)
        ]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in accepted_exts:
                continue
            rel = os.path.normpath(os.path.join(rel_dir, name)) if rel_dir else name
            if is_ignored(rel, ignore_patterns):
                continue
            full = os.path.join(dirpath, name)
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            loc = data.count(b"\n") + 1
            out.append(
                {
                    "relative_path": rel,
                    "language": LANG_BY_EXT[ext],
                    "loc": loc,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return out


def read_file(root: str, relative_path: str) -> str:
    with open(os.path.join(root, relative_path), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def safe_resolve(root: str, relative_path: str) -> str:
    """Guard against path traversal."""
    abs_root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(abs_root, relative_path))
    if not target.startswith(abs_root + os.sep) and target != abs_root:
        raise ValueError(f"Path traversal detected: {relative_path}")
    return target
