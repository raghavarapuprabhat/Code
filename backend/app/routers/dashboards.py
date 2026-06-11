"""Dashboards router — currently exposes the MD dashboard."""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.services.ado_md_service import (
    attention_for,
    drill_down_stream,
    get_dashboard,
    load_md_config,
    trigger_etl,
)

router = APIRouter()


class DrillRequest(BaseModel):
    question: str
    squad_filter: str | None = None
    snapshot_date: str | None = None


class EtlTriggerRequest(BaseModel):
    snapshot_date: str | None = None


@router.get("/md")
async def md_dashboard(snapshot_date: str | None = None) -> dict:
    dash = await get_dashboard(snapshot_date)
    cfg = load_md_config()
    attention = attention_for(dash, cfg.get("md", {}).get("attention", {}))
    return {**dash, "attention": attention}


@router.post("/md/drill")
async def md_drill(body: DrillRequest):
    if not body.question or not body.question.strip():
        raise HTTPException(400, "question is required")

    async def stream() -> AsyncIterator[dict]:
        async for ev in drill_down_stream(
            question=body.question,
            squad_filter=body.squad_filter,
            snapshot_date=body.snapshot_date,
        ):
            yield {"event": ev["type"], "data": json.dumps(ev, default=str)}

    return EventSourceResponse(stream())


@router.post("/md/etl/trigger")
async def md_etl_trigger(body: EtlTriggerRequest):
    """Force-run the daily ETL now. Useful for testing or after a config change."""
    result = await trigger_etl(snapshot_date=body.snapshot_date)
    return {"status": "ok", **result}
