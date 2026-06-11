"""Mid-loop clarification node — raises LangGraph interrupt() (§9.7B).

When the planner decides a question is the cheapest next action, the investigate node
sets ``pending_question`` and routes here. ``interrupt()`` freezes the full investigation
state (hypotheses, evidence, budget) on the checkpointer; the backend streams a ``question``
SSE event and the user's reply resumes the graph from this exact point with ``Command(resume=…)``.
The answer is folded into the ledger (and, for approvals/target resolution, into state) and
the graph loops back to Investigate — no whole-graph re-run.
"""
from __future__ import annotations

import structlog
from langgraph.types import interrupt

from ..state import SREState

logger = structlog.get_logger()

_YES = {"approve", "approved", "yes", "y", "ok", "okay", "go", "allow", "allowed", "confirm"}


async def ask_user_node(state: SREState, *, config: dict) -> dict:
    q = state.get("pending_question") or {}
    logger.info("sre_ask_user", blocks=q.get("blocks"), text=(q.get("text") or "")[:80])

    # Pause here; resumes with the user's answer (str, or {"text": ...}).
    answer = interrupt(q)
    text_ans = answer if isinstance(answer, str) else (answer or {}).get("text", "")
    text_ans = (text_ans or "").strip()

    blocks = q.get("blocks", "verdict")
    evidence = [dict(e) for e in (state.get("evidence") or [])]
    out: dict = {"pending_question": None}

    if blocks == "probe_approval":
        approved = text_ans.lower() in _YES
        out["prod_probe_approved"] = approved
        _add(evidence, "user", "prod probe approval",
             f"user {'approved' if approved else 'declined'} read-only PROD probe")
    elif blocks == "target_resolution":
        adhoc = list(state.get("adhoc_targets") or [])
        t = _parse_target(text_ans, q)
        if t:
            adhoc.append(t)
        out["adhoc_targets"] = adhoc
        _add(evidence, "user", "probe target", f"target info from user: {text_ans[:200]}")
    else:  # verdict / evidence_request
        _add(evidence, "user", "reporter answer", text_ans[:300])

    out["evidence"] = evidence
    return out


def _add(evidence: list[dict], source: str, citation: str, finding: str) -> None:
    evidence.append({
        "id": f"E{len(evidence) + 1}", "source": source,
        "citation": citation, "finding": finding, "bears_on": [],
    })


def _parse_target(answer: str, q: dict) -> dict | None:
    """Best-effort: turn a 'name=ENV_VAR' (or similar) answer into an ad-hoc ProbeTarget."""
    if "=" not in answer:
        return None
    name, _, ref = answer.partition("=")
    name, ref = name.strip(), ref.strip()
    if not name or not ref:
        return None
    qtext = (q.get("text") or "").lower()
    kind = "db" if any(k in qtext for k in ("dsn", "database", "db ", " db")) else "http"
    env = "test"
    for e in ("prod", "test", "dev", "staging", "qa"):
        if e in qtext or e in answer.lower():
            env = e
            break
    return {
        "kind": kind, "name": name, "environment": env,
        "base_url_or_dsn_ref": ref, "discovered_from": "user", "approved": env != "prod",
    }
