"""Bridge from the FastAPI backend to the standalone Code Documentation Agent.

The agent runs as an in-process LangGraph (when deployed via the website) or as
a standalone CLI (when distributed as an agent file). We import it directly here.
"""
from __future__ import annotations

import os
import sys

# Make the agents package importable when running via uvicorn from /backend.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


async def trigger_indexing(
    *,
    project_path: str,
    mode: str,
    display_name: str | None,
    requirements_areapath: str | None = None,
) -> dict:
    try:
        from agents.code_doc_agent.graph import run_indexing  # local import — heavy deps
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("langgraph"):
            raise RuntimeError(
                "Missing dependency 'langgraph'. Install backend dependencies again to enable code_doc indexing."
            ) from exc
        raise

    abs_path = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.isdir(abs_path):
        return {"status": "error", "message": f"Path not found or not a directory: {abs_path}"}

    result = await run_indexing(
        project_path=abs_path, mode=mode, display_name=display_name,
        requirements_areapath=requirements_areapath,
    )
    return {"status": "ok", **result}


async def _project_path(project_id: str) -> str | None:
    from sqlalchemy import text
    from shared.storage import get_session
    async with get_session() as session:
        row = (
            await session.execute(
                text("SELECT project_path, display_name FROM code_projects WHERE id = :id"),
                {"id": project_id},
            )
        ).first()
    return row if row else None


async def set_requirements_areapath(*, project_id: str, areapath: str) -> dict:
    """Set the ADO requirements area path and re-run an incremental index to ingest
    + trace requirements (§8.9.1)."""
    row = await _project_path(project_id)
    if not row:
        return {"status": "error", "message": f"Unknown project: {project_id}"}
    from agents.code_doc_agent.graph import run_indexing
    result = await run_indexing(
        project_path=row.project_path, mode="incremental",
        display_name=row.display_name, requirements_areapath=areapath,
    )
    return {"status": "ok", "requirements_areapath": areapath, **result}


async def run_eval(*, project_id: str) -> dict:
    """On-demand golden-Q&A eval over the currently persisted docs (§8.9.3)."""
    from sqlalchemy import text
    from shared.storage import get_session
    from agents.code_doc_agent.graph import load_config
    from agents.code_doc_agent.nodes.doc_eval import run_doc_eval

    # Pull current docs from the DB + the architecture model.
    async with get_session() as session:
        doc_rows = (
            await session.execute(
                text("SELECT doc_id, content_md FROM generated_docs WHERE project_id = :p"),
                {"p": project_id},
            )
        ).all()
        model_row = (
            await session.execute(
                text("SELECT model_json FROM architecture_models WHERE project_id = :p"),
                {"p": project_id},
            )
        ).first()
    if not doc_rows:
        return {"status": "error", "message": f"No docs indexed for project {project_id}"}
    import json
    generated_docs = {r.doc_id: r.content_md for r in doc_rows}
    model = {}
    if model_row and model_row.model_json:
        try:
            model = json.loads(model_row.model_json) if isinstance(model_row.model_json, str) else model_row.model_json
        except (json.JSONDecodeError, TypeError):
            model = {}
    result = await run_doc_eval(
        project_id=project_id, generated_docs=generated_docs, model=model, config=load_config(),
    )
    return {"status": "ok", **result}


async def record_doc_feedback(*, project_id: str, doc_id: str, section: str | None,
                              rating: int, comment: str | None) -> dict:
    """Persist reader feedback for a doc section (§8.9.9)."""
    import uuid
    from sqlalchemy import text
    from shared.storage import get_session, init_db, is_sqlite, portable_sql
    if is_sqlite():
        await init_db()
    async with get_session() as session:
        await session.execute(
            text(portable_sql("""
                INSERT INTO doc_feedback (id, project_id, doc_id, section, rating, comment)
                VALUES (:id, :pid, :doc, :sec, :rating, :comment)
            """)),
            {"id": str(uuid.uuid4()), "pid": project_id, "doc": doc_id,
             "sec": section, "rating": rating, "comment": comment},
        )
        await session.commit()
    return {"status": "ok"}


async def get_digest(*, project_id: str, limit: int = 10) -> dict:
    """Return the latest architecture change-digest entries (§8.9.4)."""
    from sqlalchemy import text
    from shared.storage import get_session
    async with get_session() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT period, digest_md, created_at FROM arch_digests
                    WHERE project_id = :p ORDER BY created_at DESC LIMIT :lim
                """),
                {"p": project_id, "lim": limit},
            )
        ).all()
    return {"project_id": project_id, "entries": [
        {"period": r.period, "digest_md": r.digest_md, "created_at": str(r.created_at)}
        for r in rows
    ]}


async def get_traceability(*, project_id: str) -> dict:
    """Structured traceability matrix for the TraceabilityPage (§13B.3 v0.7)."""
    import json
    from sqlalchemy import text
    from shared.storage import get_session
    async with get_session() as session:
        rows = (
            await session.execute(
                text("""SELECT work_item_id, title, wi_type, state, components,
                               business_rules, tests, status
                        FROM requirements_trace WHERE project_id = :p
                        ORDER BY work_item_id"""),
                {"p": project_id},
            )
        ).all()

    def _j(v):
        if not v:
            return []
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return []

    matrix = [
        {
            "work_item_id": r.work_item_id,
            "title": r.title,
            "wi_type": r.wi_type,
            "state": r.state,
            "components": _j(r.components),
            "business_rules": _j(r.business_rules),
            "tests": _j(r.tests),
            "status": r.status,
        }
        for r in rows
    ]
    return {"project_id": project_id, "matrix": matrix}


async def record_wrong_trace_link(*, project_id: str, workitem_id: str, target_kind: str,
                                  target_ref: str, method: str = "unknown") -> dict:
    """A "wrong link" 👎 vote appends a known-wrong distractor to trace_eval_links (§8.9.1)."""
    from agents.code_doc_agent.nodes.trace_eval import record_wrong_link
    return await record_wrong_link(
        project_id=project_id, workitem_id=workitem_id,
        target_kind=target_kind, target_ref=target_ref, method=method,
    )


async def get_latest_run(*, project_id: str) -> dict:
    """Run-status strip data for the Hub landing page (§13B.1 v0.7)."""
    import json
    from sqlalchemy import text
    from shared.storage import get_session, iso_ts
    async with get_session() as session:
        run = (
            await session.execute(
                text("""SELECT mode, files_indexed, summaries, gap_count, error_count,
                               errors_json, model_hash, duration_ms, status, created_at
                        FROM code_doc_runs WHERE project_id = :p
                        ORDER BY created_at DESC LIMIT 1"""),
                {"p": project_id},
            )
        ).first()
        proj = (
            await session.execute(
                text("SELECT last_indexed, display_name FROM code_projects WHERE id = :p"),
                {"p": project_id},
            )
        ).first()
    if not run:
        return {
            "project_id": project_id,
            "last_indexed": iso_ts(proj.last_indexed) if proj else None,
            "status": "never" if not (proj and proj.last_indexed) else "ok",
            "run": None,
        }
    return {
        "project_id": project_id,
        "last_indexed": iso_ts(proj.last_indexed) if proj else None,
        "status": run.status,
        "run": {
            "mode": run.mode,
            "files_indexed": run.files_indexed,
            "summaries": run.summaries,
            "gap_count": run.gap_count,
            "error_count": run.error_count,
            "errors": json.loads(run.errors_json) if run.errors_json else [],
            "model_hash": run.model_hash,
            "duration_ms": run.duration_ms,
            "created_at": str(run.created_at),
        },
    }


async def get_latest_eval(*, project_id: str) -> dict:
    from sqlalchemy import text
    from shared.storage import get_session
    async with get_session() as session:
        row = (
            await session.execute(
                text("""
                    SELECT score, total, passed, detail_json, created_at FROM doc_eval_runs
                    WHERE project_id = :p ORDER BY created_at DESC LIMIT 1
                """),
                {"p": project_id},
            )
        ).first()
    if not row:
        return {"project_id": project_id, "score": None}
    import json
    return {
        "project_id": project_id, "score": row.score, "total": row.total,
        "passed": row.passed, "created_at": str(row.created_at),
        "items": json.loads(row.detail_json) if row.detail_json else [],
    }
