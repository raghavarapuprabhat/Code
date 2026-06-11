"""TraceLink quality eval (architecture §8.9.1 v0.7).

Semantic requirement↔code linking is the feature most likely to be confidently wrong, so
it gets its own eval, separate from the Q&A harness (§8.9.3). Against a hand-labeled set
in ``trace_eval_links`` (true = correct link, false = known-wrong distractor), after every
TraceLink run we compute **precision/recall per ``method`` tier** (lexical / semantic /
llm). "Wrong link" 👎 votes from the Hub append to the set automatically, so the
measurement sharpens with use.

This module is callable from the requirements node (after producing links) and from an
on-demand path. It degrades cleanly: no labeled set → ``{"scored": False}``.
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger()


def _prf(produced: set, labeled_true: set, labeled_false: set) -> dict:
    """precision/recall against labeled links of one tier.

    precision = produced ∩ true / (produced ∩ (true ∪ false))   [of the links we made
                that are in the labeled set, how many were correct]
    recall    = produced ∩ true / true                          [of the known-correct
                links, how many did we produce]
    """
    judged = produced & (labeled_true | labeled_false)
    tp = len(produced & labeled_true)
    precision = (tp / len(judged)) if judged else None
    recall = (tp / len(labeled_true)) if labeled_true else None
    return {
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "true_positives": tp,
        "judged": len(judged),
        "labeled_true": len(labeled_true),
    }


async def evaluate_trace_links(*, project_id: str, produced_links: list[dict]) -> dict:
    """Score produced links against the labeled set, per method tier.

    ``produced_links``: [{workitem_id, target_kind, target_ref, method}]
    """
    from sqlalchemy import text
    from shared.storage import get_session

    try:
        async with get_session() as session:
            rows = (
                await session.execute(
                    text("""SELECT workitem_id, target_kind, target_ref, label, method
                            FROM trace_eval_links WHERE project_id = :p"""),
                    {"p": project_id},
                )
            ).all()
    except Exception:  # noqa: BLE001 — table may not exist yet
        return {"scored": False, "reason": "eval table unavailable"}

    if not rows:
        return {"scored": False, "reason": "no labeled links — seed trace_eval_links (~30 per project)"}

    def _key(r) -> tuple:
        return (str(r["workitem_id"] if isinstance(r, dict) else r.workitem_id),
                (r["target_kind"] if isinstance(r, dict) else r.target_kind),
                (r["target_ref"] if isinstance(r, dict) else r.target_ref))

    labeled_true = {_key(r) for r in rows if r.label}
    labeled_false = {_key(r) for r in rows if not r.label}

    tiers = {"lexical", "semantic", "llm"}
    per_tier: dict[str, dict] = {}
    for tier in sorted(tiers):
        produced = {
            (str(l["workitem_id"]), l["target_kind"], l["target_ref"])
            for l in produced_links if l.get("method") == tier
        }
        if produced or any(
            (r.method == tier) for r in rows
        ):
            per_tier[tier] = _prf(produced, labeled_true, labeled_false)

    # Overall (all tiers combined).
    all_produced = {(str(l["workitem_id"]), l["target_kind"], l["target_ref"]) for l in produced_links}
    overall = _prf(all_produced, labeled_true, labeled_false)

    logger.info("trace_eval_done", project_id=project_id,
                overall_precision=overall.get("precision"), tiers=list(per_tier.keys()))
    return {"scored": True, "overall": overall, "per_tier": per_tier,
            "labeled_total": len(rows)}


async def record_wrong_link(*, project_id: str, workitem_id: str, target_kind: str,
                            target_ref: str, method: str = "unknown") -> dict:
    """A Hub 👎 "wrong link" vote appends a known-wrong distractor to the eval set."""
    from sqlalchemy import text
    from shared.storage import get_session, init_db, is_sqlite, portable_sql
    if is_sqlite():
        await init_db()
    async with get_session() as session:
        await session.execute(
            text(portable_sql("""
                INSERT INTO trace_eval_links
                    (project_id, workitem_id, target_kind, target_ref, label, source, method)
                VALUES (:p, :w, :tk, :tr, 0, 'feedback', :m)
                ON CONFLICT (project_id, workitem_id, target_kind, target_ref) DO UPDATE SET
                    label = 0, source = 'feedback', method = excluded.method
            """)),
            {"p": project_id, "w": workitem_id, "tk": target_kind, "tr": target_ref, "m": method},
        )
        await session.commit()
    return {"ok": True}


async def persist_trace_eval(*, project_id: str, result: dict) -> None:
    """Store the latest TraceLink eval beside the doc-eval runs (reuses doc_eval_runs with
    a sentinel total of -1 so the Hub can distinguish it, or just log it). For the POC we
    log; the Hub reads it live via the on-demand endpoint."""
    logger.info("trace_eval_persisted", project_id=project_id, scored=result.get("scored"))
