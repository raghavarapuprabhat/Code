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
