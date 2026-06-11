"""HTTP endpoints specific to the Code Documentation Agent."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from shared.storage import get_session, iso_ts, portable_sql
from app.services.code_doc_service import trigger_indexing
from app.services import doc_service

router = APIRouter()


class IndexRequest(BaseModel):
    project_path: str
    mode: str = "full"   # "full" | "incremental"
    display_name: str | None = None


@router.post("/index")
async def index_project(body: IndexRequest):
    if body.mode not in {"full", "incremental"}:
        raise HTTPException(400, "mode must be 'full' or 'incremental'")
    try:
        result = await trigger_indexing(
            project_path=body.project_path,
            mode=body.mode,
            display_name=body.display_name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


@router.get("/projects")
async def list_projects() -> dict:
    async with get_session() as session:
        rows = (
            await session.execute(
                text(
                    portable_sql(
                        "SELECT id, project_path, display_name, last_indexed "
                        "FROM code_projects ORDER BY last_indexed DESC NULLS LAST"
                    )
                )
            )
        ).all()
    return {
        "projects": [
            {
                "id": r.id,
                "project_path": r.project_path,
                "display_name": r.display_name,
                "last_indexed": iso_ts(r.last_indexed),
            }
            for r in rows
        ]
    }


@router.get("/projects/{project_id}/docs")
async def list_project_docs(project_id: str) -> dict:
    """List the generated documents for a project (Documentation Hub tree)."""
    if not await doc_service.project_exists(project_id):
        raise HTTPException(404, f"Unknown project: {project_id}")
    docs = await doc_service.list_docs(project_id)
    return {"project_id": project_id, "docs": docs}


@router.get("/projects/{project_id}/docs/{doc_id}")
async def get_project_doc(
    project_id: str,
    doc_id: str,
    format: str = Query("markdown", pattern="^(markdown|html|confluence)$"),
) -> dict:
    """Fetch one generated document's content, rendered in the requested format."""
    if not await doc_service.project_exists(project_id):
        raise HTTPException(404, f"Unknown project: {project_id}")
    doc = await doc_service.get_doc(project_id, doc_id, fmt=format)  # type: ignore[arg-type]
    if doc is None:
        raise HTTPException(404, f"Unknown doc_id '{doc_id}' for project {project_id}")
    return doc
