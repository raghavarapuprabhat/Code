"""Phase 5 — Conclude: synthesize the surviving hypothesis into a cited verdict (§9.11).

Fed by the evidence ledger + hypothesis board (not raw snippets alone), it emits the
extended Verdict — classification (incl. ``external``), confidence, a root-cause
narrative, citations, and the ReAct trace for audit. The verdict is persisted so future
investigations can find this signature again (§9.17.5, foundation seed).
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import SREState
from ..tools import history

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "classify.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


def _format_evidence(ev: list[dict]) -> str:
    if not ev:
        return "(no evidence gathered — the investigation could not ground the issue)"
    out = []
    for e in ev:
        out.append(
            f"{e['id']} ({e['source']}) {e['citation']}: {e['finding']} "
            f"[bears_on: {', '.join(e.get('bears_on', [])) or '-'}]"
        )
    return "\n".join(out)


def _format_hypotheses(hyps: list[dict]) -> str:
    if not hyps:
        return "(no hypotheses)"
    rows = sorted(hyps, key=lambda h: h.get("posterior", 0), reverse=True)
    return "\n".join(
        f"{h['id']} [{h.get('status', 'open')} posterior={h.get('posterior', 0):.2f}] {h['statement']}"
        for h in rows
    )


def _format_rag(hits: list[dict]) -> str:
    if not hits:
        return "(no documentation snippets retrieved)"
    out = []
    for h in hits[:6]:
        out.append(f"[{h.get('collection', '?')}] {h.get('relative_path')}: {h.get('snippet', '')[:400]}")
    return "\n".join(out)


def _format_history(history_rounds: list[dict]) -> str:
    if not history_rounds:
        return "(no prior follow-up rounds)"
    out = []
    for i, v in enumerate(history_rounds, 1):
        out.append(f"Round {i}: classification={v.get('classification')} confidence={v.get('confidence')}")
        if v.get("questions"):
            out.append("  asked: " + "; ".join(v["questions"]))
    return "\n".join(out)


async def classify_node(state: SREState, *, config: dict) -> dict:
    llm = build_adapter_from_config(config)
    template = _load_prompt()

    issue = state.get("issue") or {}
    facts = state.get("facts") or {}
    evidence = state.get("evidence") or []
    hypotheses = state.get("hypotheses") or []
    rag_hits = state.get("rag_hits") or []
    log = state.get("investigation_log") or []
    history_rounds = state.get("classification_history") or []

    leading = max(hypotheses, key=lambda h: h.get("posterior", 0), default=None)

    prompt = (
        template
        .replace("{issue_json}", json.dumps(issue, indent=2))
        .replace("{facts_json}", json.dumps(facts, indent=2))
        .replace("{hypotheses_block}", _format_hypotheses(hypotheses))
        .replace("{evidence_block}", _format_evidence(evidence))
        .replace("{rag_block}", _format_rag(rag_hits))
        .replace("{history_block}", _format_history(history_rounds))
        .replace("{leading_posterior}", f"{leading.get('posterior', 0):.2f}" if leading else "0.00")
    )
    resp = await llm.chat([{"role": "user", "content": prompt}])
    parsed = _safe_json(resp.content) or {
        "classification": "needs_more_info",
        "confidence": 0.0,
        "rationale": "Failed to parse classifier output.",
        "questions": ["Could you re-share the issue with more detail?"],
    }

    # Normalize + backfill from the investigation.
    parsed.setdefault("root_cause", leading.get("statement", "") if leading else "")
    parsed.setdefault("citations", [e["citation"] for e in evidence if e.get("citation")][:12])
    parsed.setdefault("likely_files", _likely_files(facts, evidence, rag_hits))
    parsed["investigation_log"] = log
    if parsed.get("classification") not in {"bug", "not_a_bug", "needs_more_info", "external"}:
        parsed["classification"] = "needs_more_info"

    # A question the loop wanted to ask but couldn't pause for (CLI / batch / budget) is
    # surfaced here so it isn't lost (§9.7B fallback).
    clar = state.get("clarification") or {}
    if clar.get("text"):
        qs = list(parsed.get("questions") or [])
        if clar["text"] not in qs:
            qs.append(clar["text"])
        parsed["questions"] = qs
        if parsed["classification"] not in {"bug"} and float(parsed.get("confidence") or 0) < 0.7:
            parsed["classification"] = "needs_more_info"

    new_history = list(history_rounds) + [parsed]
    logger.info(
        "conclude_done",
        cls=parsed.get("classification"),
        confidence=parsed.get("confidence"),
        citations=len(parsed.get("citations", [])),
    )

    # Persist for find_similar_issues (best-effort; never fatal).
    if state.get("project_id"):
        await history.persist_verdict(
            project_id=state["project_id"],
            conversation_id=state.get("conversation_id"),
            facts=facts,
            verdict=parsed,
        )

    # v0.6.6: deterministic severity + blast-radius for confirmed bugs (§9.17.6).
    severity: dict = {}
    if parsed.get("classification") == "bug" and state.get("project_id"):
        try:
            from .severity import estimate_impact
            severity = await estimate_impact(
                project_id=state["project_id"],
                verdict=parsed,
                evidence=evidence,
                facts=facts,
                config=config,
            )
            logger.info("severity_done", level=severity.get("level"), hotspot=severity.get("hotspot_score"))
        except Exception:  # noqa: BLE001 — severity is advisory; never fatal
            pass

    return {
        "verdict": parsed,
        "classification_history": new_history,
        "followup_round": int(state.get("followup_round", 0)),
        "severity": severity,
    }


def _likely_files(facts: dict, evidence: list[dict], rag_hits: list[dict]) -> list[str]:
    files: list[str] = []
    for f in facts.get("failing_frames", [])[:3]:
        rel = f.get("relative_path")
        if rel and rel not in files:
            files.append(rel)
    for h in rag_hits[:3]:
        rp = h.get("relative_path", "")
        if rp and not rp.startswith("doc:") and rp not in files:
            files.append(rp)
    return files[:5]


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
