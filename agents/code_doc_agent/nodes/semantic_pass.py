"""Phase 5 — LLM-driven per-file summarization (with citations).

Each dirty file is processed: the LLM receives the AST skeleton + raw source
and returns a structured FileSummary. Output is persisted to Postgres so we
don't re-pay the cost on subsequent runs.

Large-file handling
-------------------
The prompt embeds the full source, bounded by ``_truncate`` to
``chunk_size_tokens`` (~8k tokens / ~32k chars). Files larger than that keep
only the head + tail; the middle is dropped and a ``source_truncated`` warning
is logged (the summary may miss business logic that lived in the dropped span).
This is a deliberately simple guard, NOT chunking — despite the config key name
``chunk_size_tokens``, no chunking happens yet.

TODO (future): real chunking for large files so no content is lost.
  1. Split the source on AST boundaries (per class / per method from the parsed
     FileAST) into windows of ≤ ``chunk_size_tokens``, rather than a blind
     char-slice, so each chunk is syntactically coherent.
  2. Summarize each chunk independently (reuse ``_summarize_json``), carrying the
     file path + chunk range so citations stay accurate.
  3. Merge the per-chunk FileSummaries into one: concatenate ``business_rules``
     and ``edge_cases`` (dedup by cited_method/line), union ``dependencies`` and
     ``trivial_methods``, and run a short "reduce" LLM pass to fold the per-chunk
     ``purpose`` strings into a single file-level purpose.
  4. Keep it incremental-friendly: hash per chunk so an edit to one method only
     re-summarizes the affected chunk. Bound total chunks per file to avoid a
     pathological generated file fanning out into hundreds of LLM calls.
  Until then, ``source_truncated`` warnings flag which files are affected.
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
from ..tools.json_tools import extract_json

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
                raw_source = read_file(project_path, rel_path)
                source = _truncate(raw_source, cfg.get("chunk_size_tokens", 8000))
                if len(source) < len(raw_source):
                    # The middle of the file was dropped — the summary will miss any
                    # business logic that lived there. Surface it instead of failing
                    # silently. See "Large-file handling" in the module docstring for
                    # the chunking approach that would remove this loss entirely.
                    logger.warning(
                        "source_truncated",
                        path=rel_path,
                        original_chars=len(raw_source),
                        kept_chars=len(source),
                    )
                prompt = (
                    template
                    .replace("{relative_path}", rel_path)
                    .replace("{language}", ast.get("language", "text"))
                    .replace("{ast_json}", json.dumps(ast, indent=2))
                    .replace("{source}", source)
                )
                parsed = await _summarize_json(llm, prompt)
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


# Per-file summaries embed the full source + AST in the prompt and emit purpose +
# business rules + edge cases as JSON. The default 4k cap truncates the response on
# larger files (controllers/entities), giving finish_reason=length and unparseable JSON
# — the file's summary is then lost. Give the call room and use provider JSON mode.
_SUMMARY_MAX_TOKENS = 8_000


async def _summarize_json(llm, prompt: str) -> dict | None:
    """Call the LLM for a file summary with JSON mode (where supported) and a raised
    token budget, retrying once on unparseable output. Returns None only if both
    attempts fail to yield JSON (caller logs summary_parse_failed)."""
    json_mode = getattr(llm, "supports_json_mode", lambda: False)()
    max_tokens = max(_SUMMARY_MAX_TOKENS, getattr(getattr(llm, "cfg", None), "max_tokens", 0) or 0)
    resp = await llm.chat([{"role": "user", "content": prompt}], json_mode=json_mode, max_tokens=max_tokens)
    parsed = _safe_json(resp.content)
    if parsed is not None:
        return parsed
    retry = await llm.chat(
        [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": "That was not valid JSON. Return ONLY the JSON object, no prose, no code fences."},
        ],
        json_mode=json_mode,
        max_tokens=max_tokens,
    )
    return _safe_json(retry.content)


def _truncate(source: str, max_tokens_approx: int) -> str:
    # Rough heuristic: 1 token ~= 4 chars. Truncate to keep the prompt bounded.
    max_chars = max_tokens_approx * 4
    if len(source) <= max_chars:
        return source
    head = source[: max_chars // 2]
    tail = source[-max_chars // 2:]
    return f"{head}\n\n/* ... TRUNCATED FOR LENGTH ... */\n\n{tail}"


def _safe_json(text: str) -> Any | None:
    return extract_json(text)
