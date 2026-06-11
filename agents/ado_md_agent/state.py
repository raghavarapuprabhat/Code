"""Typed state for the ADO MD Agent.

Two graphs share a few field shapes but use disjoint state TypedDicts so the
LangGraph dev UI can show them independently.
"""
from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from pydantic import BaseModel, Field


class WorkItem(BaseModel):
    id: int
    title: str = ""
    state: str = ""
    assigned_to: str | None = None
    area_path: str | None = None
    iteration_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    story_points: float | None = None
    target_date: date | None = None
    closed_date: date | None = None
    created_date: date | None = None
    work_item_type: str = ""


class SquadMetrics(BaseModel):
    squad_name: str
    snapshot_date: date
    total_workitems: int = 0
    in_progress: int = 0
    done_this_sprint: int = 0
    blocked: int = 0
    overdue: int = 0
    velocity_3sprint_avg: float = 0.0
    utilization_pct: float = 0.0


class RaidItem(BaseModel):
    squad_name: str
    type: str                       # Risk | Assumption | Issue | Dependency
    title: str
    severity: str | None = None
    owner: str | None = None
    due_date: date | None = None
    workitem_id: int | None = None


class Achievement(BaseModel):
    squad_name: str
    achievement: str
    evidence_workitem_ids: list[int] = Field(default_factory=list)


class ETLState(TypedDict, total=False):
    snapshot_date: str           # ISO date for serializability
    squads: list[dict]           # config-loaded squads
    workitems_by_squad: dict[str, list[dict]]
    metrics: list[dict]          # list[SquadMetrics]
    raids: list[dict]            # list[RaidItem]
    achievements: list[dict]     # list[Achievement]
    persisted: dict[str, int]
    errors: list[dict[str, Any]]


class DrillState(TypedDict, total=False):
    snapshot_date: str
    squad_filter: str | None
    user_question: str
    snapshot: dict               # latest snapshot rows joined into one payload
    live_extra: dict             # optional extra rows from live MCP
    answer: str
    citations: list[dict]
