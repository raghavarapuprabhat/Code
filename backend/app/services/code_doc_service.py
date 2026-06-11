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


async def trigger_indexing(*, project_path: str, mode: str, display_name: str | None) -> dict:
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

    result = await run_indexing(project_path=abs_path, mode=mode, display_name=display_name)
    return {"status": "ok", **result}
