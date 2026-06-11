"""Phase 2 — tree-sitter parse for every file in the inventory."""
from __future__ import annotations

import structlog

from ..state import CodeDocState
from ..tools.fs_tools import read_file
from ..tools.treesitter_tools import parse_file

logger = structlog.get_logger()


async def ast_extract_node(state: CodeDocState, *, config: dict) -> dict:
    project_path = state["project_path"]
    asts: dict[str, dict] = {}
    failures = 0
    for f in state["file_inventory"]:
        try:
            src = read_file(project_path, f["relative_path"])
            ast = parse_file(src, f["language"], f["relative_path"])
            asts[f["relative_path"]] = ast.model_dump()
        except Exception as e:  # noqa: BLE001
            failures += 1
            logger.warning("ast_parse_failed", path=f["relative_path"], err=str(e))
    logger.info("ast_extract_done", files=len(asts), failures=failures)
    return {"asts": asts}
