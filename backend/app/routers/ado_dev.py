"""ADO Developer Assistant HTTP surface.

POST /agents/ado_dev/chat   (SSE) — multi-turn chat
POST /agents/ado_dev/reset           — clear persisted conversation step
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.services.ado_dev_service import _save_state, stream_dev_turn

router = APIRouter()


class DevChatRequest(BaseModel):
    user_id: str
    user_name: str | None = None
    message: str = ""           # may be empty for first turn
    reset: bool = False


@router.post("/chat")
async def chat(body: DevChatRequest):
    if not body.user_id:
        raise HTTPException(400, "user_id is required")

    async def stream() -> AsyncIterator[dict]:
        async for ev in stream_dev_turn(
            user_id=body.user_id,
            user_name=body.user_name,
            user_message=body.message,
            reset=body.reset,
        ):
            yield {"event": ev["type"], "data": json.dumps(ev, default=str)}

    return EventSourceResponse(stream())


class DevResetRequest(BaseModel):
    user_id: str


@router.post("/reset")
async def reset(body: DevResetRequest):
    await _save_state(body.user_id, {"step": "greet"})
    return {"status": "ok"}
