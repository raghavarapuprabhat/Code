"""Typed state for the SRE Fixer Agent."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class FileEdit(BaseModel):
    relative_path: str
    new_content: str                           # full new file content (deterministic)
    rationale: str = ""


class FixPlan(BaseModel):
    summary: str
    root_cause: str
    edits: list[FileEdit] = Field(default_factory=list)
    test_command_key: str | None = None        # one of fixer.test_commands keys
    notes: str = ""


class TestResult(BaseModel):
    passed: bool
    command: list[str]
    duration_ms: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    failed_tests: list[str] = Field(default_factory=list)


class PRInfo(BaseModel):
    url: str
    branch: str
    pr_id: int


class FixerState(TypedDict, total=False):
    project_id: str                            # Code Doc project_id (= repo path key)
    repo_path: str                             # absolute git working dir
    azure_repo: dict                           # {"organization_url","project","repository_id","target_branch"}
    handoff: dict                              # original SRE handoff payload (issue+verdict+rag_hits)

    plan: dict                                 # FixPlan
    plan_history: list[dict]                   # FixPlan from each attempt
    last_test: dict                            # TestResult
    test_history: list[dict]
    attempt: int                               # 1-based
    failure_analysis: str                      # LLM analysis of last test failure

    branch_name: str
    pr: dict                                   # PRInfo

    status: Literal["planning", "applied", "tests_failed", "tests_passed",
                    "branch_created", "pr_opened", "raised_human", "error"]
    error: str | None
    audit_trail: list[dict[str, Any]]          # node-by-node breadcrumb
