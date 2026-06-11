"""CLI: `python -m agents.sre_fixer_agent --handoff handoff.json --project <id>
                                          --ado-project Foo --ado-repo Bar`.

handoff.json shape (matches the SRE Agent's `handoff` event payload):
{
  "issue": {...},
  "verdict": {"classification": "bug", "confidence": 0.9, "likely_files": [...], "rationale": "..."},
  "rag_hits": [...]
}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.sre_fixer_agent.graph import run_fix  # noqa: E402


async def _run(args) -> None:
    with open(args.handoff) as fh:
        handoff = json.load(fh)
    azure = {
        "project": args.ado_project,
        "repository_id": args.ado_repo,
        "target_branch": args.target_branch,
    }
    result = await run_fix(
        project_id=args.project,
        handoff=handoff,
        azure_repo=azure,
        repo_path=args.repo_path,
    )
    print(json.dumps({
        "status": result.get("status"),
        "branch": result.get("branch_name"),
        "pr": result.get("pr"),
        "error": result.get("error"),
        "audit_trail": result.get("audit_trail"),
    }, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(prog="sre_fixer_agent")
    p.add_argument("--project", required=True, help="project_id from Code Doc Agent")
    p.add_argument("--handoff", required=True, help="path to JSON file with the SRE handoff payload")
    p.add_argument("--ado-project", required=True, help="Azure DevOps project name or id")
    p.add_argument("--ado-repo", required=True, help="Azure Repos repository name or id")
    p.add_argument("--target-branch", default="refs/heads/main")
    p.add_argument("--repo-path", help="Override the working-tree path")
    args = p.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
