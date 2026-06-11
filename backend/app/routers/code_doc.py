"""HTTP endpoints specific to the Code Documentation Agent."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from shared.storage import get_session, iso_ts, portable_sql
from app.services.code_doc_service import (
    get_digest,
    get_latest_eval,
    record_doc_feedback,
    run_eval,
    set_requirements_areapath,
    trigger_indexing,
)
from app.services import doc_service

router = APIRouter()


class IndexRequest(BaseModel):
    project_path: str
    mode: str = "full"   # "full" | "incremental"
    display_name: str | None = None


class RequirementsRequest(BaseModel):
    areapath: str


class FeedbackRequest(BaseModel):
    doc_id: str
    section: str | None = None
    rating: int          # 1 (down) .. 5 (up)
    comment: str | None = None


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


@router.post("/projects/{project_id}/requirements")
async def set_requirements(project_id: str, body: RequirementsRequest):
    """Set ADO requirements area path(s); triggers ingest + trace (§8.9.1)."""
    if not body.areapath:
        raise HTTPException(400, "areapath is required")
    result = await set_requirements_areapath(project_id=project_id, areapath=body.areapath)
    if result.get("status") == "error":
        raise HTTPException(404, result.get("message", "failed"))
    return result


@router.post("/projects/{project_id}/eval")
async def run_doc_eval(project_id: str):
    """Run the golden-Q&A eval over the current docs (§8.9.3)."""
    result = await run_eval(project_id=project_id)
    if result.get("status") == "error":
        raise HTTPException(404, result.get("message", "failed"))
    return result


@router.get("/projects/{project_id}/eval/latest")
async def latest_eval(project_id: str):
    """Latest eval score for the Hub badge (§8.9.3)."""
    return await get_latest_eval(project_id=project_id)


@router.post("/projects/{project_id}/docs/{doc_id}/feedback")
async def doc_feedback(project_id: str, doc_id: str, body: FeedbackRequest):
    """Reader feedback per document/section (§8.9.9)."""
    if body.doc_id != doc_id:
        body.doc_id = doc_id
    return await record_doc_feedback(
        project_id=project_id, doc_id=doc_id, section=body.section,
        rating=body.rating, comment=body.comment,
    )


@router.get("/projects/{project_id}/digest")
async def project_digest(project_id: str, limit: int = Query(10, ge=1, le=50)):
    """Architecture change-digest entries (§8.9.4)."""
    return await get_digest(project_id=project_id, limit=limit)


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
