"""Read-only git tools — blame + recent log, for regression hunting (§9.7).

GitPython is imported lazily so a developer who hasn't installed it (or a project
that isn't a git repo) gets a graceful "history unavailable" note rather than a
crash — the investigation simply leans on static evidence instead.
"""
from __future__ import annotations

import os

import structlog

from .rag import _find_by_basename, _project_root

logger = structlog.get_logger()


def _open_repo(root: str):
    try:
        from git import InvalidGitRepositoryError, NoSuchPathError, Repo
    except ImportError:
        return None, "git history unavailable (GitPython not installed)"
    try:
        repo = Repo(root, search_parent_directories=True)
        return repo, None
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None, "git history unavailable (project is not a git repository)"
    except Exception as e:  # noqa: BLE001
        return None, f"git history unavailable ({e})"


async def _resolve(project_id: str, relative_path: str) -> tuple[str | None, str | None]:
    """Return (repo_root, abs_file_path) resolving a basename when needed."""
    root = await _project_root(project_id)
    if not root:
        return None, None
    abs_root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(abs_root, relative_path))
    if not (target == abs_root or target.startswith(abs_root + os.sep)) or not os.path.isfile(target):
        target = _find_by_basename(abs_root, os.path.basename(relative_path))
    return abs_root, target


async def git_blame(
    project_id: str, relative_path: str, start_line: int, end_line: int
) -> str:
    """Last change + author + commit for the suspect lines."""
    root, target = await _resolve(project_id, relative_path)
    if not root or not target:
        return f"(file not found: {relative_path})"
    repo, err = _open_repo(root)
    if err:
        return err
    rel = os.path.relpath(target, repo.working_tree_dir)
    try:
        blame = repo.blame(
            "HEAD", rel, L=f"{max(start_line, 1)},{max(end_line, start_line)}"
        )
    except Exception as e:  # noqa: BLE001
        return f"(blame failed for {rel}: {e})"
    out: list[str] = [f"blame {rel}:{start_line}-{end_line}"]
    seen: set[str] = set()
    for commit, lines in blame:
        sha = commit.hexsha[:8]
        if sha in seen:
            continue
        seen.add(sha)
        when = commit.committed_datetime.date().isoformat()
        summary = (commit.summary or "")[:80]
        out.append(f"  {sha} {when} {commit.author.name}: {summary} ({len(lines)} line(s))")
    return "\n".join(out)


async def git_log_recent(
    project_id: str, relative_path: str | None = None, *, max_count: int = 10
) -> str:
    """Recent commits touching the area — regression hunting."""
    root = await _project_root(project_id)
    if not root:
        return "(project root unknown)"
    repo, err = _open_repo(os.path.abspath(root))
    if err:
        return err
    paths = None
    if relative_path:
        _, target = await _resolve(project_id, relative_path)
        if target:
            paths = os.path.relpath(target, repo.working_tree_dir)
    try:
        commits = list(repo.iter_commits("HEAD", paths=paths, max_count=max_count))
    except Exception as e:  # noqa: BLE001
        return f"(git log failed: {e})"
    if not commits:
        return "(no commits found for this path)"
    out = [f"recent commits{f' for {paths}' if paths else ''}:"]
    for c in commits:
        out.append(
            f"  {c.hexsha[:8]} {c.committed_datetime.date().isoformat()} "
            f"{c.author.name}: {(c.summary or '')[:80]}"
        )
    return "\n".join(out)
