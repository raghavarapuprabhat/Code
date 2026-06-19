"""Phase 4.5 — API surface analysis: endpoints, DTOs, auth, sample payloads.

Runs after cross_file and before verify. Two sub-passes:
  1. Deterministic: scan asts for Spring/Next.js annotations (no LLM).
  2. LLM enrichment: link endpoints <-> DTOs, infer status codes, generate samples.
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState
from ..tools.json_tools import extract_json
from ..tools.treesitter_tools import extract_api_endpoints_from_asts

logger = structlog.get_logger()

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "api_surface.md")


def _load_prompt() -> str:
    with open(_PROMPT_PATH) as fh:
        return fh.read()


def _safe_json(text: str):
    return extract_json(text)


def _compact_summaries(summaries: dict[str, dict]) -> dict[str, str]:
    return {
        path: s.get("purpose", "")
        for path, s in list(summaries.items())[:60]
    }


async def api_surface_node(state: CodeDocState, *, config: dict) -> dict:
    asts = state.get("asts") or {}
    if not asts:
        logger.info("api_surface_node: no ASTs in state, skipping")
        return {"api_endpoints": [], "dto_classes": []}

    # --- Pass 1: deterministic extraction from AST dicts ---
    raw_endpoints, raw_dtos = extract_api_endpoints_from_asts(asts)

    if not raw_endpoints and not raw_dtos:
        logger.info("api_surface_node: no endpoints or DTOs found — non-REST or pure frontend project")
        return {"api_endpoints": [], "dto_classes": []}

    logger.info(
        "api_surface_raw",
        endpoints=len(raw_endpoints),
        dtos=len(raw_dtos),
    )

    # --- Pass 2: LLM enrichment ---
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    summaries = state.get("file_summaries") or {}
    prompt = (
        template
        .replace("{endpoints_json}", json.dumps(raw_endpoints, indent=2)[:15_000])
        .replace("{dtos_json}", json.dumps(raw_dtos, indent=2)[:15_000])
        .replace("{summaries_json}", json.dumps(_compact_summaries(summaries), indent=2)[:8_000])
    )

    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content)

    if not parsed:
        logger.warning("api_surface_node: LLM returned unparseable JSON — falling back to raw data")
        return {
            "api_endpoints": raw_endpoints,
            "dto_classes": raw_dtos,
        }

    enriched = parsed.get("enriched_endpoints", raw_endpoints)
    dto_catalog = parsed.get("dto_catalog", raw_dtos)

    logger.info(
        "api_surface_done",
        enriched_endpoints=len(enriched),
        dto_catalog=len(dto_catalog),
    )
    return {
        "api_endpoints": enriched,
        "dto_classes": dto_catalog,
    }
