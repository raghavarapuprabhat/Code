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

from app.services.sre_service import steer_triage, stream_triage, triage_csv_text

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
    """Resume a paused investigation with the user's answer to a mid-loop question (§9.7B)."""
    return _sse(stream_triage(
        project_id=body.project_id or "",
        user_message=body.answer,
        conversation_id=conversation_id,
        user_id=body.user_id,
    ))


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
