"""Generic SSE chat endpoint used by all conversational agents.

POST /agents/{agent_id}/chat
Body: {"message": "...", "conversation_id": "...optional...", "scope_key": "..."}
Response: text/event-stream of structured JSON events.
"""
from __future__ import annotations

import json
import structlog
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.services.agent_runner import run_agent_chat

logger = structlog.get_logger()
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    scope_key: str | None = None
    user_id: str | None = "local-dev"


@router.post("/{agent_id}/chat")
async def chat(agent_id: str, body: ChatRequest):
    if agent_id not in {"code_doc", "sre", "ado_dev"}:
        raise HTTPException(404, f"Unknown agent_id: {agent_id}")

    async def event_stream() -> AsyncIterator[dict]:
        try:
            async for event in run_agent_chat(
                agent_id=agent_id,
                message=body.message,
                conversation_id=body.conversation_id,
                scope_key=body.scope_key,
                user_id=body.user_id,
            ):
                yield {"event": event["type"], "data": json.dumps(event)}
        except Exception as exc:
            logger.exception("event_stream_error", agent=agent_id, error=str(exc))
            yield {
                "event": "error",
                "data": json.dumps({"type": "error", "message": str(exc)}),
            }

    return EventSourceResponse(event_stream())
