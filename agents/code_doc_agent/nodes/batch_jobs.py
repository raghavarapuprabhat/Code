"""Phase 4.7 — Batch job and scheduled task analysis.

Runs after api_surface. Two sub-passes:
  1. Deterministic: scan ASTs for @Scheduled, Spring Batch interfaces, Quartz jobs,
     CommandLineRunner, and Node.js cron libraries.
  2. LLM enrichment: decode schedules, infer data sources/sinks, error handling strategy.
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState
from ..tools.json_tools import extract_json
from ..tools.treesitter_tools import extract_batch_jobs_from_asts

logger = structlog.get_logger()

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "batch_jobs.md")


def _load_prompt() -> str:
    with open(_PROMPT_PATH) as fh:
        return fh.read()


def _safe_json(text: str):
    return extract_json(text)


def _compact_summaries(summaries: dict[str, dict]) -> dict[str, str]:
    return {
        path: s.get("purpose", "") + " | deps: " + ", ".join(s.get("dependencies", [])[:5])
        for path, s in list(summaries.items())[:60]
    }


async def batch_jobs_node(state: CodeDocState, *, config: dict) -> dict:
    asts = state.get("asts") or {}
    if not asts:
        logger.info("batch_jobs_node: no ASTs in state, skipping")
        return {"batch_jobs": []}

    # --- Pass 1: deterministic detection ---
    raw_jobs = extract_batch_jobs_from_asts(asts)

    if not raw_jobs:
        logger.info("batch_jobs_node: no scheduled/batch jobs detected")
        return {"batch_jobs": []}

    logger.info("batch_jobs_raw", count=len(raw_jobs))

    # --- Pass 2: LLM enrichment ---
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    summaries = state.get("file_summaries") or {}
    prompt = (
        template
        .replace("{batch_jobs_json}", json.dumps(raw_jobs, indent=2)[:20_000])
        .replace("{summaries_json}", json.dumps(_compact_summaries(summaries), indent=2)[:8_000])
    )

    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content)

    if not parsed:
        logger.warning("batch_jobs_node: LLM returned unparseable JSON — falling back to raw data")
        return {"batch_jobs": raw_jobs}

    enriched = parsed.get("enriched_jobs", raw_jobs)
    logger.info("batch_jobs_done", count=len(enriched))
    return {"batch_jobs": enriched}
