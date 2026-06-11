"""Typed state for the SRE Agent.

v0.6 (§9.4): the single-shot classifier is reworked into an agentic, hypothesis-driven
investigator. The state grows the working memory an investigation needs — normalized
facts, a ranked hypothesis board, an evidence ledger, the ReAct trace, and a budget —
while preserving the shipped fields the FastAPI layer already persists (issue,
classification_history, followup_round, rag_hits, verdict, handoff).
"""
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


class Frame(BaseModel):
    """One parsed stack-trace frame, resolved to a relative path + line when possible."""
    raw: str                            # the original frame line
    symbol: str | None = None           # e.g. "OrderService.price"
    relative_path: str | None = None    # e.g. "OrderService.java" (best-effort)
    line: int | None = None


class IssueFacts(BaseModel):
    """Normalized from the raw report during Understand (§9.6)."""
    error_signature: str = ""           # "NullPointerException @ OrderService.price:142"
    exception_type: str | None = None
    failing_frames: list[Frame] = Field(default_factory=list)
    component: str | None = None        # suspected module / area
    environment: str | None = None
    symptoms: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    id: str
    statement: str                      # "order is null on cache miss; repo returns empty"
    prior: float = 0.3                  # initial plausibility 0..1
    posterior: float = 0.3              # updated as evidence arrives
    status: Literal["open", "supported", "refuted"] = "open"
    supporting: list[str] = Field(default_factory=list)  # evidence ids
    refuting: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    id: str
    source: Literal[
        "code", "doc", "git", "callgraph", "flow", "similar_issue", "user",
        # v0.4 (live probe + arch-model) and v0.6 (observability) sources are reserved
        # here so the ledger schema is forward-compatible; they are unused by the
        # static-tool foundation.
        "api", "db", "architecture", "logs", "metrics", "deploy",
    ]
    citation: str                       # "OrderService.java:142" | "doc:04_flows#checkout" | "commit abc123"
    finding: str                        # what it shows
    bears_on: list[str] = Field(default_factory=list)  # hypothesis ids it supports / refutes


class InvestigationStep(BaseModel):
    """One ReAct turn — for the SSE trace + audit (§9.13)."""
    n: int
    thought: str = ""
    action: str = ""                    # tool name + args, rendered
    observation: str = ""


class Budget(BaseModel):
    max_steps: int = 8                  # investigation iterations
    max_tool_calls: int = 16
    max_tokens: int = 60_000
    used_steps: int = 0
    used_tool_calls: int = 0


class Verdict(BaseModel):
    """Extends the shipped model: it now carries the investigation, not just a label (§9.11)."""
    classification: Literal["bug", "not_a_bug", "needs_more_info", "external"]
    confidence: float = 0.0
    root_cause: str = ""                # narrative tied to the evidence ledger
    rationale: str = ""
    citations: list[str] = Field(default_factory=list)   # file:line / doc_id / commit
    likely_files: list[str] = Field(default_factory=list)
    suggested_owner: str | None = None
    next_step: str = ""
    questions: list[str] = Field(default_factory=list)   # only when needs_more_info
    investigation_log: list[dict] = Field(default_factory=list)  # the ReAct trace


class SREState(TypedDict, total=False):
    project_id: str
    issue: dict                 # IssueIntake
    facts: dict                 # IssueFacts                (new)
    hypotheses: list[dict]      # list[Hypothesis]          (new)
    evidence: list[dict]        # list[Evidence]            (new)
    investigation_log: list[dict]   # list[InvestigationStep] (new)
    budget: dict                # Budget                    (new)
    rag_hits: list[dict]        # list[RagHit]
    classification_history: list[dict]   # list[Verdict] across follow-up rounds
    verdict: dict               # final Verdict
    followup_round: int
    user_message: str           # latest message from user (interactive mode)
    messages: list[dict]        # full chat-style transcript for this triage
    handoff: dict | None        # bug packet for the SRE Fixer if classification == bug
    conversation_id: str        # set by the backend for similar-issue dedup / persistence
    batch: bool                 # CSV/batch mode — tighter budget, no interactive pauses


class RagHit(BaseModel):
    relative_path: str
    score: float
    snippet: str
    collection: str = ""        # "code" | "docs" — which Chroma collection it came from
    metadata: dict[str, Any] = Field(default_factory=dict)
