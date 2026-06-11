"""Typed state for the SRE Agent."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class IssueIntake(BaseModel):
    title: str = ""
    description: str = ""
    stack_trace: str | None = None
    environment: str | None = None
    repro_steps: str | None = None
    additional_context: str | None = None


class RagHit(BaseModel):
    relative_path: str
    score: float
    snippet: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Verdict(BaseModel):
    classification: Literal["bug", "not_a_bug", "needs_more_info"]
    confidence: float = 0.0
    rationale: str = ""
    likely_files: list[str] = Field(default_factory=list)
    suggested_owner: str | None = None
    next_step: str = ""
    questions: list[str] = Field(default_factory=list)


class SREState(TypedDict, total=False):
    project_id: str
    issue: dict                 # IssueIntake
    rag_hits: list[dict]        # list[RagHit]
    classification_history: list[dict]   # list[Verdict] across follow-up rounds
    verdict: dict               # final Verdict
    followup_round: int
    user_message: str           # latest message from user (interactive mode)
    messages: list[dict]        # full chat-style transcript for this triage
    handoff: dict | None        # payload for SRE Fixer if classification == bug
