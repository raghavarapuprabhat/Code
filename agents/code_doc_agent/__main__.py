"""CLI entry point: `python -m code_doc_agent <project_path> [--incremental]`.

Lets a developer run this agent standalone with their own model credentials,
no website backend required. The output `.docs/` folder is written into the
target project directory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# When run from this folder directly, make the parent (`agents/`) importable
# so that imports like `shared.storage` resolve.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.code_doc_agent.graph import run_indexing  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(prog="code_doc_agent")
    parser.add_argument("project_path", help="Absolute path to the codebase to document")
    parser.add_argument("--incremental", action="store_true", help="Only re-process changed files")
    parser.add_argument("--name", help="Display name for this project", default=None)
    args = parser.parse_args()

    result = asyncio.run(
        run_indexing(
            project_path=os.path.abspath(os.path.expanduser(args.project_path)),
            mode="incremental" if args.incremental else "full",
            display_name=args.name,
        )
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
