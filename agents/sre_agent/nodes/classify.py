"""Phase 3 — classify the issue as bug / not_a_bug / needs_more_info."""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import SREState

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "classify.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


def _format_rag(hits: list[dict]) -> str:
    if not hits:
        return "(no documentation snippets retrieved — the indexed code may not cover this area)"
    out = []
    for h in hits:
        out.append(f"### {h.get('relative_path')} (score={h.get('score', 0):.2f})")
        out.append(h.get("snippet", "")[:1200])
        out.append("")
    return "\n".join(out)


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no prior follow-up rounds)"
    out = []
    for i, v in enumerate(history, 1):
        qs = v.get("questions", [])
        out.append(f"Round {i}: classification={v.get('classification')} confidence={v.get('confidence')}")
        if qs:
            out.append("Questions asked: " + "; ".join(qs))
    return "\n".join(out)


async def classify_node(state: SREState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    issue = state.get("issue") or {}
    rag_hits = state.get("rag_hits") or []
    history = state.get("classification_history") or []

    prompt = template.format(
        issue_json=json.dumps(issue, indent=2),
        rag_block=_format_rag(rag_hits),
        history_block=_format_history(history),
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content) or {
        "classification": "needs_more_info",
        "confidence": 0.0,
        "rationale": "Failed to parse classifier output.",
        "questions": ["Could you re-share the issue with more detail?"],
    }
    parsed.setdefault("likely_files", [h.get("relative_path") for h in rag_hits[:3]])

    new_history = list(history) + [parsed]
    logger.info(
        "sre_classify_done",
        cls=parsed.get("classification"),
        confidence=parsed.get("confidence"),
    )
    return {
        "verdict": parsed,
        "classification_history": new_history,
        "followup_round": int(state.get("followup_round", 0)),
    }


def _safe_json(text: str):
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
