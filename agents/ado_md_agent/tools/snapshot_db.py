"""Postgres helpers for reading/writing the MD snapshot tables."""
from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy import text

from shared.storage import get_session

logger = structlog.get_logger()


# ----------------------------------------------------------------------
# Writers (called by ETL)
# ----------------------------------------------------------------------
async def upsert_squad_snapshot(metrics_rows: list[dict]) -> int:
    if not metrics_rows:
        return 0
    async with get_session() as session:
        for m in metrics_rows:
            await session.execute(
                text(
                    """
                    INSERT INTO squad_snapshot (
                        snapshot_date, squad_name, total_workitems, in_progress,
                        done_this_sprint, blocked, overdue,
                        velocity_3sprint_avg, utilization_pct
                    ) VALUES (
                        :d, :s, :tot, :ip, :done, :bl, :ov, :vel, :util
                    )
                    ON CONFLICT (snapshot_date, squad_name) DO UPDATE SET
                        total_workitems = EXCLUDED.total_workitems,
                        in_progress = EXCLUDED.in_progress,
                        done_this_sprint = EXCLUDED.done_this_sprint,
                        blocked = EXCLUDED.blocked,
                        overdue = EXCLUDED.overdue,
                        velocity_3sprint_avg = EXCLUDED.velocity_3sprint_avg,
                        utilization_pct = EXCLUDED.utilization_pct
                    """
                ),
                {
                    "d": m["snapshot_date"],
                    "s": m["squad_name"],
                    "tot": m.get("total_workitems", 0),
                    "ip": m.get("in_progress", 0),
                    "done": m.get("done_this_sprint", 0),
                    "bl": m.get("blocked", 0),
                    "ov": m.get("overdue", 0),
                    "vel": m.get("velocity_3sprint_avg", 0),
                    "util": m.get("utilization_pct", 0),
                },
            )
        await session.commit()
    return len(metrics_rows)


async def replace_raid_for_date(snapshot_date: str, raids: list[dict]) -> int:
    async with get_session() as session:
        await session.execute(
            text("DELETE FROM raid_snapshot WHERE snapshot_date = :d"),
            {"d": snapshot_date},
        )
        for r in raids:
            await session.execute(
                text(
                    """
                    INSERT INTO raid_snapshot
                        (snapshot_date, squad_name, type, title, severity, owner, due_date, workitem_id)
                    VALUES (:d, :s, :t, :ti, :sev, :own, :due, :wi)
                    """
                ),
                {
                    "d": snapshot_date,
                    "s": r["squad_name"],
                    "t": r["type"],
                    "ti": r.get("title"),
                    "sev": r.get("severity"),
                    "own": r.get("owner"),
                    "due": r.get("due_date"),
                    "wi": r.get("workitem_id"),
                },
            )
        await session.commit()
    return len(raids)


async def replace_achievements_for_date(snapshot_date: str, items: list[dict]) -> int:
    async with get_session() as session:
        await session.execute(
            text("DELETE FROM key_achievement WHERE snapshot_date = :d"),
            {"d": snapshot_date},
        )
        for a in items:
            await session.execute(
                text(
                    """
                    INSERT INTO key_achievement (snapshot_date, squad_name, achievement, evidence_workitem_ids)
                    VALUES (:d, :s, :a, :ev)
                    """
                ),
                {
                    "d": snapshot_date,
                    "s": a["squad_name"],
                    "a": a["achievement"],
                    "ev": a.get("evidence_workitem_ids") or [],
                },
            )
        await session.commit()
    return len(items)


# ----------------------------------------------------------------------
# Readers (called by dashboard + drill-down)
# ----------------------------------------------------------------------
async def latest_snapshot_date() -> str | None:
    async with get_session() as session:
        row = (await session.execute(
            text("SELECT MAX(snapshot_date) AS d FROM squad_snapshot")
        )).first()
    if row and row.d:
        return row.d.isoformat() if isinstance(row.d, date) else str(row.d)
    return None


async def load_dashboard(snapshot_date: str | None = None) -> dict[str, Any]:
    snap = snapshot_date or await latest_snapshot_date()
    if not snap:
        return {"snapshot_date": None, "squads": [], "raids": [], "achievements": []}
    async with get_session() as session:
        squads = (await session.execute(
            text(
                "SELECT squad_name, total_workitems, in_progress, done_this_sprint, "
                "       blocked, overdue, velocity_3sprint_avg, utilization_pct "
                "FROM squad_snapshot WHERE snapshot_date = :d ORDER BY squad_name"
            ),
            {"d": snap},
        )).all()
        raids = (await session.execute(
            text(
                "SELECT squad_name, type, title, severity, owner, due_date, workitem_id "
                "FROM raid_snapshot WHERE snapshot_date = :d "
                "ORDER BY CASE severity WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, squad_name"
            ),
            {"d": snap},
        )).all()
        achievements = (await session.execute(
            text(
                "SELECT squad_name, achievement, evidence_workitem_ids "
                "FROM key_achievement WHERE snapshot_date = :d ORDER BY squad_name"
            ),
            {"d": snap},
        )).all()
    return {
        "snapshot_date": snap,
        "squads": [
            {
                "squad_name": r.squad_name,
                "total_workitems": r.total_workitems,
                "in_progress": r.in_progress,
                "done_this_sprint": r.done_this_sprint,
                "blocked": r.blocked,
                "overdue": r.overdue,
                "velocity_3sprint_avg": float(r.velocity_3sprint_avg or 0),
                "utilization_pct": float(r.utilization_pct or 0),
            }
            for r in squads
        ],
        "raids": [
            {
                "squad_name": r.squad_name,
                "type": r.type,
                "title": r.title,
                "severity": r.severity,
                "owner": r.owner,
                "due_date": iso_ts(r.due_date),
                "workitem_id": r.workitem_id,
            }
            for r in raids
        ],
        "achievements": [
            {
                "squad_name": r.squad_name,
                "achievement": r.achievement,
                "evidence_workitem_ids": list(r.evidence_workitem_ids or []),
            }
            for r in achievements
        ],
    }
