"""SRE Agent HTTP surface.

POST /agents/sre/triage          (SSE) — interactive triage, multi-turn
POST /agents/sre/triage-csv      — batch triage; returns a triaged CSV
"""
from __future__ import annotations

import csv
import io
import json
from typing import AsyncIterator

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.services.sre_service import (
    claim_answer,
    file_ado_bug,
    get_calibration_stats,
    get_conversation_state,
    record_verdict_outcome,
    run_verify_fix,
    steer_triage,
    stream_triage,
    triage_csv_text,
)

router = APIRouter()


class TriageRequest(BaseModel):
    project_id: str
    message: str
    conversation_id: str | None = None
    user_id: str | None = "local-dev"


class AnswerRequest(BaseModel):
    answer: str
    project_id: str | None = ""
    user_id: str | None = "local-dev"


class SteerRequest(BaseModel):
    action: str                       # pin | inject | kill
    hypothesis_id: str | None = None
    statement: str | None = None      # for inject


def _sse(stream) -> EventSourceResponse:
    async def gen() -> AsyncIterator[dict]:
        async for ev in stream:
            yield {"event": ev["type"], "data": json.dumps(ev)}
    return EventSourceResponse(gen())


@router.post("/triage")
async def triage(body: TriageRequest):
    if not body.project_id:
        raise HTTPException(400, "project_id is required")
    return _sse(stream_triage(
        project_id=body.project_id,
        user_message=body.message,
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    ))


@router.post("/triage/{conversation_id}/answer")
async def answer(conversation_id: str, body: AnswerRequest):
    """Resume a paused investigation with the user's answer (§9.7B v0.7).

    Concurrency: the first answer wins — a compare-and-set flips paused → running. A
    second `/answer` for the same paused question returns 409 Conflict.
    """
    claimed = await claim_answer(conversation_id)
    if not claimed:
        raise HTTPException(409, "conversation is not awaiting an answer (already answered or not paused)")
    return _sse(stream_triage(
        project_id=body.project_id or "",
        user_message=body.answer,
        conversation_id=conversation_id,
        user_id=body.user_id,
    ))


@router.get("/triage/{conversation_id}")
async def triage_state(conversation_id: str):
    """Conversation lifecycle state for UI re-hydration (§9.7B v0.7):
    running | paused | concluded | expired (+ the open question when paused)."""
    return await get_conversation_state(conversation_id)


@router.post("/triage/{conversation_id}/steer")
async def steer(conversation_id: str, body: SteerRequest):
    """Pin / inject / kill a hypothesis on a live investigation (§9.17.8)."""
    if body.action not in {"pin", "inject", "kill"}:
        raise HTTPException(400, "action must be pin | inject | kill")
    return await steer_triage(
        conversation_id=conversation_id,
        action=body.action,
        hypothesis_id=body.hypothesis_id,
        statement=body.statement,
    )


class OutcomeRequest(BaseModel):
    project_id: str
    classification: str               # bug | not_a_bug | external | needs_more_info
    confidence: float
    outcome: str                      # confirmed | overturned | unresolved
    outcome_source: str               # human_review | pr_merged | verify_fix | ado_state
    root_cause_final: str = ""


class VerifyFixRequest(BaseModel):
    project_id: str
    pr_url: str | None = None


class AdoFileRequest(BaseModel):
    project_id: str
    dry_run: bool = False


@router.post("/verdicts/{conversation_id}/ado-file")
async def ado_file(conversation_id: str, body: AdoFileRequest):
    """File (or dry-run) an ADO Bug for a confirmed verdict (§9.17.7).

    Set dry_run=true to preview without actually creating a work item.
    Requires sre.ado_writeback.enabled=true in config AND ADO_PROJECT env var.
    """
    return await file_ado_bug(
        conversation_id=conversation_id,
        project_id=body.project_id,
        dry_run=body.dry_run,
    )


@router.post("/verdicts/{conversation_id}/outcome")
async def record_outcome(conversation_id: str, body: OutcomeRequest):
    """Record a verdict outcome from any feedback channel (§9.17.5).

    outcome_source values: human_review | pr_merged | verify_fix | ado_state
    outcome values: confirmed | overturned | unresolved
    """
    if body.outcome not in {"confirmed", "overturned", "unresolved"}:
        raise HTTPException(400, "outcome must be confirmed | overturned | unresolved")
    if body.outcome_source not in {"human_review", "pr_merged", "verify_fix", "ado_state"}:
        raise HTTPException(400, "outcome_source must be human_review | pr_merged | verify_fix | ado_state")
    return await record_verdict_outcome(
        conversation_id=conversation_id,
        project_id=body.project_id,
        classification=body.classification,
        confidence=body.confidence,
        outcome=body.outcome,
        outcome_source=body.outcome_source,
        root_cause_final=body.root_cause_final,
    )


@router.get("/calibration/{project_id}")
async def calibration(project_id: str):
    """Brier score + calibration bands for a project (§9.17.5).

    Lower Brier score = better confidence calibration.
    """
    if not project_id:
        raise HTTPException(400, "project_id is required")
    return await get_calibration_stats(project_id=project_id)


@router.post("/triage/{conversation_id}/verify-fix")
async def verify_fix(conversation_id: str, body: VerifyFixRequest):
    """Re-run original probes after fix deploys; confirm symptoms resolved (§9.17.4)."""
    if not body.project_id:
        raise HTTPException(400, "project_id is required")
    result = await run_verify_fix(
        conversation_id=conversation_id,
        project_id=body.project_id,
        pr_url=body.pr_url,
    )
    return result


@router.post("/triage-csv")
async def triage_csv_endpoint(
    project_id: str = Form(...),
    file: UploadFile = File(...),
):
    if not project_id:
        raise HTTPException(400, "project_id is required")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty CSV upload")

    result = await triage_csv_text(project_id=project_id, csv_bytes=raw)
    rows = result["rows"]

    # Stream back as a CSV download.
    if not rows:
        raise HTTPException(400, "no rows produced — check the input columns")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"content-disposition": 'attachment; filename="triaged.csv"'},
    )
