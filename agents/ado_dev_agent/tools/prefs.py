"""User preference helpers (last areapath/iteration + step persistence)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from shared.storage import get_session, portable_sql

AGENT_NAME = "ado_dev_agent"


async def load_prefs(user_id: str) -> dict[str, Any]:
    async with get_session() as session:
        row = (await session.execute(
            text(
                "SELECT last_areapath, last_iteration, preferences "
                "FROM user_preferences WHERE user_id = :u AND agent_name = :a"
            ),
            {"u": user_id, "a": AGENT_NAME},
        )).first()
    if not row:
        return {"last_areapath": None, "last_iteration": None, "preferences": {}}
    prefs = row.preferences or {}
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except json.JSONDecodeError:
            prefs = {}
    return {
        "last_areapath": row.last_areapath,
        "last_iteration": row.last_iteration,
        "preferences": prefs,
    }


async def save_prefs(
    user_id: str,
    *,
    last_areapath: str | None = None,
    last_iteration: str | None = None,
    preferences: dict | None = None,
) -> None:
    async with get_session() as session:
        await session.execute(
            text(
                portable_sql(
                    """
                INSERT INTO user_preferences (user_id, agent_name, last_areapath, last_iteration, preferences)
                VALUES (:u, :a, :ap, :it, CAST(:p AS JSONB))
                ON CONFLICT (user_id, agent_name) DO UPDATE SET
                    last_areapath = COALESCE(EXCLUDED.last_areapath, user_preferences.last_areapath),
                    last_iteration = COALESCE(EXCLUDED.last_iteration, user_preferences.last_iteration),
                    preferences = COALESCE(EXCLUDED.preferences, user_preferences.preferences),
                    updated_at = now()
                """
                )
            ),
            {
                "u": user_id,
                "a": AGENT_NAME,
                "ap": last_areapath,
                "it": last_iteration,
                "p": json.dumps(preferences) if preferences is not None else None,
            },
        )
        await session.commit()
