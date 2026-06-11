"""Phase 3 — Hypothesize: a ranked differential diagnosis (§9.6, §9.8).

Cheap to enumerate, expensive to confirm — so we rank. The node turns the facts +
grounding into a small board of competing root-cause hypotheses with priors; the
Investigate loop then spends its budget confirming/refuting the leading one. Prior
confirmed issues with the same signature are surfaced as context so a repeat
regression starts with a higher prior.
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import SREState
from ..tools import history

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "hypothesize.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


def _format_rag(hits: list[dict], limit: int = 6) -> str:
    if not hits:
        return "(no grounding snippets retrieved)"
    out = []
    for h in hits[:limit]:
        out.append(f"[{h.get('collection', '?')}] {h.get('relative_path')}: {h.get('snippet', '')[:400]}")
    return "\n".join(out)


async def hypothesize_node(state: SREState, *, config: dict) -> dict:
    # Skip re-hypothesizing on follow-up rounds that already have a board.
    if state.get("hypotheses"):
        return {}

    facts = state.get("facts") or {}
    project_id = state.get("project_id", "")

    similar = "(skipped)"
    if project_id:
        similar = await history.find_similar_issues(
            project_id,
            facts.get("error_signature", ""),
            exception_type=facts.get("exception_type"),
            exclude_conversation_id=state.get("conversation_id"),
        )

    llm = build_adapter_from_config(config)
    prompt = (
        _load_prompt()
        .replace("{facts_json}", json.dumps(facts, indent=2))
        .replace("{rag_block}", _format_rag(state.get("rag_hits") or []))
        .replace("{similar_block}", similar)
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content) or {}
    raw = parsed.get("hypotheses", []) if isinstance(parsed, dict) else parsed

    hypotheses: list[dict] = []
    for i, h in enumerate(raw[:5], 1):
        prior = _clamp(h.get("prior", 0.3))
        hypotheses.append(
            {
                "id": h.get("id") or f"H{i}",
                "statement": h.get("statement", "").strip(),
                "prior": prior,
                "posterior": prior,
                "status": "open",
                "supporting": [],
                "refuting": [],
            }
        )
    if not hypotheses:
        hypotheses = [
            {
                "id": "H1",
                "statement": "Unknown root cause — investigate the cited failing location.",
                "prior": 0.3,
                "posterior": 0.3,
                "status": "open",
                "supporting": [],
                "refuting": [],
            }
        ]

    logger.info("hypothesize_done", n=len(hypotheses))
    return {"hypotheses": hypotheses}


def _clamp(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.3


def _safe_json(text: str):
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    # Accept either an object {"hypotheses": [...]} or a bare array.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = text.find(open_c), text.rfind(close_c)
        if s >= 0 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                continue
    return None
