"""CLI: a tiny REPL for the Developer Assistant.

Usage:
    python -m agents.ado_dev_agent --user me@corp.com --name "Me Surname"
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

from agents.ado_dev_agent.graph import run_turn  # noqa: E402


async def repl(user_id: str, user_name: str | None) -> None:
    state: dict = {
        "user_id": user_id,
        "user_name": user_name or user_id,
        "step": "greet",
    }
    print(">>> ADO Developer Assistant (type Ctrl-D to exit)")
    # First turn: pass empty message to trigger greet.
    state["user_message"] = ""
    out = await run_turn(state=state)
    state.update(out)
    print(f"\nassistant: {out.get('response_text')}\n")
    if out.get("status_report"):
        print("status:")
        print(json.dumps(out["status_report"], indent=2, default=str))

    while True:
        try:
            line = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        state["user_message"] = line
        out = await run_turn(state=state)
        state.update(out)
        print(f"\nassistant: {out.get('response_text')}\n")
        if out.get("status_report"):
            print("status:")
            print(json.dumps(out["status_report"], indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(prog="ado_dev_agent")
    p.add_argument("--user", required=True, help="user_id (UPN/email) used for prefs lookup")
    p.add_argument("--name", help="Display name (used as ADO assigned-to filter)")
    args = p.parse_args()
    asyncio.run(repl(args.user, args.name))


if __name__ == "__main__":
    main()
