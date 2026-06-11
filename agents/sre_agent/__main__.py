"""CLI for the SRE Agent.

Examples:
    # Single-issue triage from a JSON file:
    python -m agents.sre_agent --project <project_id> --issue ./issue.json

    # CSV batch:
    python -m agents.sre_agent --project <project_id> --csv ./incidents.csv \\
        --out ./triaged.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.sre_agent.graph import run_triage, triage_csv  # noqa: E402


async def _run(args) -> None:
    if args.csv:
        with open(args.csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        out_rows = await triage_csv(project_id=args.project, rows=rows)
        out_path = args.out or args.csv.replace(".csv", ".triaged.csv")
        if out_rows:
            with open(out_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
                w.writeheader()
                w.writerows(out_rows)
            print(f"Wrote {len(out_rows)} triaged rows to {out_path}")
        else:
            print("No rows triaged.")
        return

    raw = ""
    if args.issue:
        with open(args.issue) as fh:
            raw = fh.read()
    elif args.text:
        raw = args.text
    else:
        raw = sys.stdin.read()

    result = await run_triage(project_id=args.project, user_message=raw)
    print(json.dumps(result.get("verdict"), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="sre_agent")
    parser.add_argument("--project", required=True, help="project_id from the Code Doc Agent")
    parser.add_argument("--issue", help="Path to a JSON or text file describing one issue")
    parser.add_argument("--text", help="Inline issue text")
    parser.add_argument("--csv", help="Path to a CSV of incidents")
    parser.add_argument("--out", help="Output CSV path (defaults next to input)")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
