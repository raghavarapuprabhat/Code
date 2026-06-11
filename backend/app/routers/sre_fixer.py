"""SRE Fixer Agent HTTP surface.

POST /agents/sre_fixer/run    (SSE) — runs the patch+test+PR pipeline once.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.services.sre_fixer_service import stream_fixer_run

router = APIRouter()


class AzureRepoTarget(BaseModel):
    project: str = Field(..., description="ADO project name or id")
    repository_id: str = Field(..., description="ADO repository name or id")
    target_branch: str = "refs/heads/main"


class FixerRunRequest(BaseModel):
    project_id: str
    handoff: dict[str, Any]
    azure_repo: AzureRepoTarget
    repo_path: str | None = None


@router.post("/run")
async def run(body: FixerRunRequest):
    if not body.handoff.get("verdict"):
        raise HTTPException(400, "handoff.verdict is required")
    if (body.handoff["verdict"].get("classification") or "").lower() != "bug":
        raise HTTPException(
            400,
            "Fixer only runs on confirmed bugs (handoff.verdict.classification must be 'bug')",
        )

    async def stream() -> AsyncIterator[dict]:
        async for ev in stream_fixer_run(
            project_id=body.project_id,
            handoff=body.handoff,
            azure_repo=body.azure_repo.model_dump(),
            repo_path=body.repo_path,
        ):
            yield {"event": ev["type"], "data": json.dumps(ev)}

    return EventSourceResponse(stream())
