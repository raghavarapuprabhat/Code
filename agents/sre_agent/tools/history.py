"""Outcome memory — persist verdicts and find prior issues with the same signature.

``find_similar_issues`` gives the loop priors from the past: an error signature
seen and confirmed before is strong evidence for the same root cause again. The
table is small and forward-compatible with v0.6 calibration / outcome tracking
(§9.17.5); for the foundation it simply accumulates each concluded verdict.
"""
from __future__ import annotations

import json
import uuid

import structlog
from sqlalchemy import text

from shared.storage import get_session, init_db, is_sqlite, portable_sql

logger = structlog.get_logger()

_PG_DDL = """
CREATE TABLE IF NOT EXISTS sre_verdicts (
    id                TEXT PRIMARY KEY,
    project_id        TEXT,
    conversation_id   TEXT,
    error_signature   TEXT,
    exception_type    TEXT,
    component         TEXT,
    classification    TEXT,
    confidence        REAL,
    root_cause        TEXT,
    citations_json    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _ensure_table(session) -> None:
    if is_sqlite():
        await init_db()
    else:
        await session.execute(text(_PG_DDL))


async def persist_verdict(
    *,
    project_id: str,
    conversation_id: str | None,
    facts: dict,
    verdict: dict,
) -> None:
    """Upsert the concluded verdict so future investigations can learn from it.

    Keyed by conversation_id when present (interactive multi-turn overwrites the
    same row); batch rows get a fresh id.
    """
    row_id = conversation_id or str(uuid.uuid4())
    payload = {
        "id": row_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "error_signature": (facts or {}).get("error_signature", ""),
        "exception_type": (facts or {}).get("exception_type"),
        "component": (facts or {}).get("component"),
        "classification": verdict.get("classification"),
        "confidence": float(verdict.get("confidence") or 0.0),
        "root_cause": verdict.get("root_cause", ""),
        "citations_json": json.dumps(verdict.get("citations", [])),
    }
    sql = portable_sql(
        """
        INSERT INTO sre_verdicts
            (id, project_id, conversation_id, error_signature, exception_type,
             component, classification, confidence, root_cause, citations_json)
        VALUES
            (:id, :project_id, :conversation_id, :error_signature, :exception_type,
             :component, :classification, :confidence, :root_cause, :citations_json)
        ON CONFLICT (id) DO UPDATE SET
            error_signature = EXCLUDED.error_signature,
            exception_type = EXCLUDED.exception_type,
            component = EXCLUDED.component,
            classification = EXCLUDED.classification,
            confidence = EXCLUDED.confidence,
            root_cause = EXCLUDED.root_cause,
            citations_json = EXCLUDED.citations_json
        """
    )
    try:
        async with get_session() as session:
            await _ensure_table(session)
            await session.execute(text(sql), payload)
            await session.commit()
    except Exception as e:  # noqa: BLE001 — persistence is best-effort, never fatal
        logger.warning("sre_persist_verdict_failed", err=str(e))


async def find_similar_issues(
    project_id: str,
    signature: str,
    *,
    exception_type: str | None = None,
    exclude_conversation_id: str | None = None,
    limit: int = 5,
) -> str:
    """Prior triaged issues sharing the error signature + their verdicts."""
    clauses = ["project_id = :p"]
    params: dict = {"p": project_id, "lim": limit}
    if exception_type:
        clauses.append("exception_type = :etype")
        params["etype"] = exception_type
    elif signature:
        clauses.append("error_signature LIKE :sig")
        params["sig"] = f"%{signature[:40]}%"
    if exclude_conversation_id:
        clauses.append("(conversation_id IS NULL OR conversation_id <> :excl)")
        params["excl"] = exclude_conversation_id
    sql = (
        "SELECT classification, confidence, root_cause, error_signature, created_at "
        "FROM sre_verdicts WHERE " + " AND ".join(clauses) +
        " ORDER BY created_at DESC LIMIT :lim"
    )
    try:
        async with get_session() as session:
            await _ensure_table(session)
            rows = (await session.execute(text(sql), params)).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("sre_find_similar_failed", err=str(e))
        return "(similar-issue lookup unavailable)"
    if not rows:
        return "(no prior triaged issues with this signature)"
    out = [f"{len(rows)} prior issue(s) with a similar signature:"]
    for r in rows:
        out.append(
            f"  - [{r.classification} @ {float(r.confidence or 0):.0%}] "
            f"{(r.root_cause or '')[:140]}  (sig: {r.error_signature})"
        )
    return "\n".join(out)
