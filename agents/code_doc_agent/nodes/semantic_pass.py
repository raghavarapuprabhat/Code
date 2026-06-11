"""Phase 5 — LLM-driven per-file summarization (with citations).

Each dirty file is processed: the LLM receives the AST skeleton + raw source
and returns a structured FileSummary. Output is persisted to Postgres so we
don't re-pay the cost on subsequent runs.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog
from sqlalchemy import text

from shared.llm_adapter import build_adapter_from_config
from shared.storage import get_session, portable_sql
from ..state import CodeDocState
from ..tools.fs_tools import read_file

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "file_summary.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH, "r") as fh:
        return fh.read()


async def semantic_pass_node(state: CodeDocState, *, config: dict) -> dict:
    cfg = config["code_doc"]
    parallel = int(cfg.get("parallel_files", 4))
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    dirty = state.get("dirty_files", [])
    asts = state.get("asts", {})
    project_path = state["project_path"]
    pid = state["project_id"]
    existing = state.get("file_summaries", {}) or {}

    sem = asyncio.Semaphore(parallel)
    results: dict[str, dict] = dict(existing)

    async def process(rel_path: str) -> None:
        async with sem:
            try:
                ast = asts.get(rel_path) or {}
                source = read_file(project_path, rel_path)
                source = _truncate(source, cfg.get("chunk_size_tokens", 8000))
                prompt = (
                    template
                    .replace("{relative_path}", rel_path)
                    .replace("{language}", ast.get("language", "text"))
                    .replace("{ast_json}", json.dumps(ast, indent=2))
                    .replace("{source}", source)
                )
                resp = await llm.chat([{"role": "user", "content": prompt}])
                parsed = _safe_json(resp.content)
                if parsed is None:
                    logger.warning("summary_parse_failed", path=rel_path)
                    return
                parsed["relative_path"] = rel_path
                results[rel_path] = parsed
            except Exception as e:  # noqa: BLE001
                logger.exception("semantic_pass_failed", path=rel_path, err=str(e))

    await asyncio.gather(*(process(p) for p in dirty))

    # Persist summaries + update file hashes for incremental mode.
    inventory_by_path = {f["relative_path"]: f for f in state["file_inventory"]}
    async with get_session() as session:
        for rel_path, summary in results.items():
            if rel_path not in inventory_by_path:
                continue
            await session.execute(
                text(
                    portable_sql(
                        """
                    INSERT INTO code_file_summaries (project_id, relative_path, summary_json)
                    VALUES (:p, :r, CAST(:s AS JSONB))
                    ON CONFLICT (project_id, relative_path)
                    DO UPDATE SET summary_json = EXCLUDED.summary_json, updated_at = now()
                    """
                    )
                ),
                {"p": pid, "r": rel_path, "s": json.dumps(summary)},
            )
            f = inventory_by_path[rel_path]
            await session.execute(
                text(
                    portable_sql(
                        """
                    INSERT INTO code_files (project_id, relative_path, language, loc, last_hash, last_analyzed_at)
                    VALUES (:p, :r, :lang, :loc, :h, now())
                    ON CONFLICT (project_id, relative_path) DO UPDATE
                    SET language = EXCLUDED.language, loc = EXCLUDED.loc,
                        last_hash = EXCLUDED.last_hash, last_analyzed_at = now()
                    """
                    )
                ),
                {"p": pid, "r": rel_path, "lang": f["language"], "loc": f["loc"], "h": f["sha256"]},
            )
        await session.commit()

    logger.info(
        "semantic_pass_done",
        processed=len(dirty),
        summaries_total=len(results),
    )
    return {"file_summaries": results}


def _truncate(source: str, max_tokens_approx: int) -> str:
    # Rough heuristic: 1 token ~= 4 chars. Truncate to keep the prompt bounded.
    max_chars = max_tokens_approx * 4
    if len(source) <= max_chars:
        return source
    head = source[: max_chars // 2]
    tail = source[-max_chars // 2:]
    return f"{head}\n\n/* ... TRUNCATED FOR LENGTH ... */\n\n{tail}"


def _safe_json(text: str) -> Any | None:
    text = text.strip()
    if text.startswith("```"):
        # strip code fences
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # Try to find the first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
