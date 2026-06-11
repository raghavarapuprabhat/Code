"""Bridge from FastAPI backend to the standalone SRE Fixer Agent.

The fixer is intentionally not a chat agent — it runs as a one-shot pipeline
and emits structured progress events for the UI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncIterator

import structlog

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = structlog.get_logger()


async def stream_fixer_run(
    *,
    project_id: str,
    handoff: dict,
    azure_repo: dict,
    repo_path: str | None = None,
) -> AsyncIterator[dict]:
    """Run the fixer in the background and yield audit-trail breadcrumbs.

    LangGraph's ainvoke returns the final state — to stream node-by-node we
    interleave it with periodic polling of the audit_trail length. For POC,
    we cheat by running the fixer to completion and streaming the audit_trail
    after the fact, plus a heartbeat so the UI doesn't time out.
    """
    from agents.sre_fixer_agent.graph import run_fix  # local import — heavy deps

    yield {"type": "start", "project_id": project_id}

    # Run the graph in a task so we can emit heartbeats while it works.
    task = asyncio.create_task(
        run_fix(
            project_id=project_id,
            handoff=handoff,
            azure_repo=azure_repo,
            repo_path=repo_path,
        )
    )
    last_heartbeat = 0
    while not task.done():
        await asyncio.sleep(2)
        last_heartbeat += 2
        yield {"type": "heartbeat", "elapsed_seconds": last_heartbeat}

    try:
        result = task.result()
    except Exception as e:  # noqa: BLE001
        logger.exception("fixer_run_failed")
        yield {"type": "error", "message": str(e)}
        return

    for step in result.get("audit_trail") or []:
        yield {"type": "step", **step}

    yield {
        "type": "final",
        "status": result.get("status"),
        "branch": result.get("branch_name"),
        "pr": result.get("pr"),
        "error": result.get("error"),
    }
