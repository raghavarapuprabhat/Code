"""Typed state for the ADO Developer Assistant.

A single graph, driven by a `step` field that the conversational backend
persists across user turns. Each call processes ONE user turn.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

Step = Literal[
    "greet",
    "await_areapath",
    "await_intent",
    "await_what_done",
    "await_consent",
    "done",
]


class StatusReport(TypedDict, total=False):
    assigned: int
    in_progress: int
    overdue: int
    planned_this_week: int
    done_this_week: int
    velocity_3sprint_avg: float
    sprint_utilization_pct: float
    overdue_items: list[dict[str, Any]]
    not_started_items: list[dict[str, Any]]
    in_progress_items: list[dict[str, Any]]
    planned_items: list[dict[str, Any]]
    action_items: list[str]


class CandidateUpdate(TypedDict, total=False):
    workitem_id: int
    title: str
    state: str
    proposed_comment: str
    proposed_state_transition: str | None
    confidence: float
    reason: str


class DevState(TypedDict, total=False):
    user_id: str
    user_name: str
    user_message: str

    # Persisted between turns by the backend (loaded from user_preferences):
    step: Step
    last_areapath: str | None
    last_iteration: str | None
    intent: Literal["status", "update", "unknown"] | None
    what_done_text: str
    candidate_updates: list[dict]    # CandidateUpdate
    pending_apply: bool

    # Outputs of this turn:
    response_text: str
    status_report: dict | None       # StatusReport when intent == "status"
    needs_consent: bool
    applied: list[dict]              # what we updated in MCP
    error: str | None
