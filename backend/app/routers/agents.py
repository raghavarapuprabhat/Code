"""Static catalog of available agents — used by the UI to render the picker."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

AGENTS = [
    {
        "id": "code_doc",
        "name": "Code Documentation Agent",
        "description": "Deep, exhaustive documentation generation for Java + React codebases.",
        "endpoints": {
            "index": "/agents/code_doc/index",
            "chat": "/agents/code_doc/chat",
            "projects": "/agents/code_doc/projects",
        },
        "status": "available",
    },
    {
        "id": "sre",
        "name": "SRE Agent",
        "description": "Triage incoming issues against generated documentation.",
        "endpoints": {
            "triage": "/agents/sre/triage",
            "triage_csv": "/agents/sre/triage-csv",
        },
        "status": "available",
    },
    {
        "id": "sre_fixer",
        "name": "SRE Fixer Agent",
        "description": "Auto-patch confirmed bugs and open Azure Repos PRs (human-in-the-loop).",
        "endpoints": {"run": "/agents/sre_fixer/run"},
        "status": "available",
    },
    {
        "id": "ado_md",
        "name": "ADO Managing Director Assistant",
        "description": "Daily portfolio snapshot (utilization, RAID, achievements) with drill-down chat.",
        "endpoints": {
            "dashboard": "/dashboards/md",
            "drill": "/dashboards/md/drill",
            "etl_trigger": "/dashboards/md/etl/trigger",
        },
        "status": "available",
    },
    {
        "id": "ado_dev",
        "name": "ADO Developer Assistant",
        "description": "Daily standup-style status reports and consent-gated workitem updates.",
        "endpoints": {
            "chat": "/agents/ado_dev/chat",
            "reset": "/agents/ado_dev/reset",
        },
        "status": "available",
    },
]


@router.get("")
async def list_agents() -> dict:
    return {"agents": AGENTS}
