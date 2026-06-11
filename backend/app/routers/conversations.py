"""Inspect/manage conversations and their summaries."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from shared.storage import get_session, iso_ts

router = APIRouter()


@router.get("/{conversation_id}/summary")
async def get_summary(conversation_id: str) -> dict:
    async with get_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT running_summary, message_count, last_summarized_at "
                    "FROM conversation_summaries WHERE conversation_id = :id"
                ),
                {"id": conversation_id},
            )
        ).first()
    if not row:
        raise HTTPException(404, "conversation not found")
    return {
        "conversation_id": conversation_id,
        "summary": row.running_summary,
        "message_count": row.message_count,
        "last_summarized_at": iso_ts(row.last_summarized_at),
    }


@router.get("")
async def list_conversations(agent_name: str | None = None, scope_key: str | None = None) -> dict:
    async with get_session() as session:
        sql = "SELECT id, agent_name, scope_key, title, updated_at FROM conversations"
        clauses = []
        params: dict = {}
        if agent_name:
            clauses.append("agent_name = :agent")
            params["agent"] = agent_name
        if scope_key:
            clauses.append("scope_key = :scope")
            params["scope"] = scope_key
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT 100"
        rows = (await session.execute(text(sql), params)).all()
    return {
        "conversations": [
            {
                "id": r.id,
                "agent_name": r.agent_name,
                "scope_key": r.scope_key,
                "title": r.title,
                "updated_at": iso_ts(r.updated_at),
            }
            for r in rows
        ]
    }
