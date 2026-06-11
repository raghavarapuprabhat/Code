"""Git operations with hard-coded safety rails.

Rules enforced here (NOT in config):
- Branch name MUST start with the configured prefix (default: fix/sre-).
- NEVER pushes to a protected branch.
- NEVER force-pushes.
- NEVER deletes branches.

These rules cannot be overridden by the LLM or the agent state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from git import GitCommandError, Repo


class GitSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitSafetyConfig:
    branch_prefix: str = "fix/sre-"
    protected_branches: tuple[str, ...] = ("main", "master", "release", "develop")
    forbid_force_push: bool = True
    forbid_branch_delete: bool = True


_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")


def _validate_branch_name(name: str, cfg: GitSafetyConfig) -> None:
    if not name or not _BRANCH_NAME_RE.match(name):
        raise GitSafetyError(f"Invalid branch name: {name!r}")
    if not name.startswith(cfg.branch_prefix):
        raise GitSafetyError(
            f"Refusing to create branch {name!r}: must start with {cfg.branch_prefix!r}"
        )
    base = name.split("/", 1)[0]
    if base in cfg.protected_branches or name in cfg.protected_branches:
        raise GitSafetyError(f"Refusing to operate on protected branch: {name!r}")


def open_repo(repo_path: str) -> Repo:
    repo = Repo(repo_path)
    if repo.bare:
        raise GitSafetyError("Refusing to operate on a bare repository.")
    return repo


def current_branch(repo: Repo) -> str:
    try:
        return repo.active_branch.name
    except TypeError:
        # Detached HEAD
        return repo.head.commit.hexsha[:8]


def create_branch(repo: Repo, branch_name: str, *, base: str | None, cfg: GitSafetyConfig) -> str:
    _validate_branch_name(branch_name, cfg)
    if branch_name in [h.name for h in repo.heads]:
        repo.git.checkout(branch_name)
        return branch_name
    base_ref = base or repo.head.reference.name
    repo.git.checkout("-b", branch_name, base_ref)
    return branch_name


def commit_all(repo: Repo, message: str, *, paths: Iterable[str] | None = None) -> str | None:
    if paths is None:
        repo.git.add(A=True)
    else:
        for p in paths:
            repo.git.add(p)
    if not repo.is_dirty(index=True, working_tree=False, untracked_files=False):
        # Nothing staged.
        return None
    repo.index.commit(message)
    return repo.head.commit.hexsha


def push_branch(repo: Repo, branch_name: str, *, cfg: GitSafetyConfig, remote: str = "origin") -> None:
    _validate_branch_name(branch_name, cfg)
    args = ["push", "--set-upstream", remote, branch_name]
    # Explicitly never pass --force / --force-with-lease.
    if cfg.forbid_force_push and any(a in {"--force", "-f", "--force-with-lease"} for a in args):
        raise GitSafetyError("Force push blocked by safety policy.")
    try:
        repo.git.execute(["git", *args])
    except GitCommandError as e:
        raise GitSafetyError(f"git push failed: {e}") from e


# Branch delete is intentionally NOT exposed.
