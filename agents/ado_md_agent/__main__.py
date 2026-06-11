"""CLI for the ADO MD agent.

Examples:
    # Run today's ETL snapshot
    python -m agents.ado_md_agent etl

    # Run for a specific date
    python -m agents.ado_md_agent etl --date 2026-04-29

    # Drill-down from CLI
    python -m agents.ado_md_agent drill --q "Why is Payments behind?"
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

from agents.ado_md_agent.graph import run_drill, run_etl  # noqa: E402


async def _cmd_etl(args) -> None:
    out = await run_etl(snapshot_date=args.date)
    print(json.dumps(out, indent=2, default=str))


async def _cmd_drill(args) -> None:
    out = await run_drill(question=args.q, squad_filter=args.squad, snapshot_date=args.date)
    print(json.dumps(out, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(prog="ado_md_agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    etl = sub.add_parser("etl", help="Run the daily snapshot ETL")
    etl.add_argument("--date", help="ISO snapshot date (default: today)")

    drill = sub.add_parser("drill", help="Run a single drill-down question")
    drill.add_argument("--q", required=True, help="The MD's question")
    drill.add_argument("--squad", help="Restrict scope to one squad")
    drill.add_argument("--date", help="Use a specific snapshot date")

    args = p.parse_args()
    if args.cmd == "etl":
        asyncio.run(_cmd_etl(args))
    else:
        asyncio.run(_cmd_drill(args))


if __name__ == "__main__":
    main()
