"""Phase 8 — DocCritique (§8.8.5): LLM-as-judge quality gate before persist.

Each generated document is scored 1–5 on a 5-criterion rubric. Any criterion < 4 marks
the doc as failing; failing docs get a visible `quality_notes` banner prepended (the
POC's lighter alternative to full section regeneration — bounded by `max_critique_loops`).

To keep cost bounded we critique only the *architecture-reconstruction* docs by default
(02, 09–12) plus the management overview, since those are the ones most prone to
ungrounded claims. The set is configurable via `code_doc.critique_docs`.

The node loops at most `max_critique_loops` times: it does not itself regenerate (the POC
appends a banner instead), so it always terminates after one scoring pass, but the graph
edge supports a future regen loop without changes here.
"""
from __future__ import annotations

import json
import os

import structlog

from shared.docs import doc_metadata
from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "doc_critique.md")

_DEFAULT_CRITIQUE_DOCS = [
    "01_management_overview", "02_architecture", "09_deployment_infra",
    "10_architecture_decisions", "11_quality_hotspots", "12_external_integrations",
]
_PASS_THRESHOLD = 4


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


def _banner(notes: str, failing: list[str]) -> str:
    crit = ", ".join(failing) if failing else "quality"
    return (
        f"> ⚠ **Quality notes (DocCritique):** this document scored below the bar on "
        f"_{crit}_. {notes.strip()} Treat flagged sections with care.\n\n"
    )


async def doc_critique_node(state: CodeDocState, *, config: dict) -> dict:
    cfg = config.get("code_doc", {}) or {}
    if not cfg.get("doc_critique", True):
        return {"critique": {"skipped": True}}

    docs: dict[str, str] = dict(state.get("generated_docs") or {})
    if not docs:
        return {"critique": {}}

    target_ids = cfg.get("critique_docs") or _DEFAULT_CRITIQUE_DOCS
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    results: dict[str, dict] = {}
    updated = dict(docs)
    for doc_id in target_ids:
        content = docs.get(doc_id)
        if not content:
            continue
        meta = doc_metadata(doc_id)
        prompt = (
            template
            .replace("{doc_id}", doc_id)
            .replace("{audience}", meta.get("audience", "developer"))
            .replace("{doc_md}", content[:40_000])
        )
        try:
            resp = await llm.chat([{"role": "user", "content": prompt}])
            parsed = _safe_json(resp.content) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc_critique_call_failed", doc_id=doc_id, err=str(exc))
            continue

        scores = parsed.get("scores", {}) or {}
        failing = parsed.get("failing_criteria") or [
            k for k, v in scores.items() if isinstance(v, (int, float)) and v < _PASS_THRESHOLD
        ]
        results[doc_id] = {"scores": scores, "failing": failing, "notes": parsed.get("notes", "")}
        if failing:
            updated[doc_id] = _banner(parsed.get("notes", ""), failing) + content

    n_failed = sum(1 for r in results.values() if r["failing"])
    logger.info("doc_critique_done", critiqued=len(results), failed=n_failed)
    return {
        "generated_docs": updated,
        "critique": results,
        "critique_loops": int(state.get("critique_loops", 0)) + 1,
    }


def _safe_json(text: str):
    text = (text or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
