"""Outcome memory and Brier-score calibration (§9.17.5).

Outcome channels:
  human_review  — someone explicitly marks the verdict correct/wrong via the API
  pr_merged     — the Fixer's PR was merged (implies the bug was real → confirmed)
  verify_fix    — verify-after-fix ran: verified→confirmed, still_failing→unresolved
  ado_state     — ADO work item closed as "fixed" or "won't fix" (confirmed/overturned)

Brier score: lower is better (0 = perfect calibration, 1 = perfectly miscalibrated).
  BS = (1/N) * Σ (confidence_i − actual_i)²
  actual_i = 1.0 if classification=="bug" and outcome=="confirmed"
           = 1.0 if classification=="not_a_bug" and outcome=="overturned" (model was right)
           = 0.0 otherwise (model was wrong)

Weekly calibration runs via a CronJob in the backend (§9.15); this module provides the
logic so it can also be called ad-hoc via the API.
"""
from __future__ import annotations

import math
from typing import Any


def _actual(classification: str, outcome: str) -> float | None:
    """Convert (classification, outcome) to a binary 0/1 ground truth.

    Returns None for 'unresolved' rows — skip those in Brier calculation.
    """
    if outcome == "unresolved":
        return None
    cls = (classification or "").lower()
    out = (outcome or "").lower()
    if cls == "bug":
        return 1.0 if out == "confirmed" else 0.0
    # not_a_bug or external: model is correct if verdict was overturned
    # (meaning reviewer agreed it wasn't a bug).
    return 1.0 if out == "overturned" else 0.0


def compute_brier_score(rows: list[dict[str, Any]]) -> dict:
    """Given a list of verdict_outcomes rows, return calibration stats."""
    scored = []
    skipped = 0
    for r in rows:
        actual = _actual(r.get("classification", ""), r.get("outcome", ""))
        if actual is None:
            skipped += 1
            continue
        conf = float(r.get("confidence") or 0.5)
        scored.append((conf, actual))

    n = len(scored)
    if n == 0:
        return {
            "n": 0, "skipped": skipped,
            "brier_score": None, "accuracy": None,
            "mean_confidence": None,
        }

    brier = sum((c - a) ** 2 for c, a in scored) / n
    accuracy = sum(1 for c, a in scored if round(c) == a) / n
    mean_conf = sum(c for c, _ in scored) / n
    return {
        "n": n,
        "skipped": skipped,
        "brier_score": round(brier, 4),
        "accuracy": round(accuracy, 4),
        "mean_confidence": round(mean_conf, 4),
        # Calibration band breakdown (for the calibration chart).
        "bands": _calibration_bands(scored),
    }


def _calibration_bands(scored: list[tuple[float, float]]) -> list[dict]:
    """Group predictions into 0.1-wide confidence bins; compute mean accuracy per bin."""
    bins: dict[int, list[float]] = {}
    for conf, actual in scored:
        b = min(int(conf * 10), 9)
        bins.setdefault(b, []).append(actual)
    out = []
    for b in sorted(bins):
        items = bins[b]
        lo = b / 10
        out.append({
            "confidence_low": lo,
            "confidence_high": lo + 0.1,
            "n": len(items),
            "mean_actual": round(sum(items) / len(items), 4),
        })
    return out


async def record_outcome(
    *,
    session,
    conversation_id: str,
    project_id: str,
    classification: str,
    confidence: float,
    outcome: str,                            # confirmed | overturned | unresolved
    outcome_source: str,                     # human_review | pr_merged | verify_fix | ado_state
    root_cause_final: str = "",
) -> dict:
    """Upsert a verdict outcome row. Call from any feedback channel."""
    from sqlalchemy import text

    if outcome not in {"confirmed", "overturned", "unresolved"}:
        raise ValueError(f"Invalid outcome {outcome!r}")
    if outcome_source not in {"human_review", "pr_merged", "verify_fix", "ado_state"}:
        raise ValueError(f"Invalid outcome_source {outcome_source!r}")

    await session.execute(
        text("""
            INSERT INTO verdict_outcomes
                (conversation_id, project_id, classification, confidence,
                 outcome, outcome_source, root_cause_final)
            VALUES
                (:cid, :pid, :cls, :conf, :outcome, :source, :rc)
            ON CONFLICT(conversation_id) DO UPDATE SET
                outcome          = excluded.outcome,
                outcome_source   = excluded.outcome_source,
                root_cause_final = excluded.root_cause_final
        """),
        {
            "cid": conversation_id, "pid": project_id,
            "cls": classification, "conf": confidence,
            "outcome": outcome, "source": outcome_source,
            "rc": root_cause_final,
        },
    )
    await session.commit()
    return {"conversation_id": conversation_id, "outcome": outcome, "source": outcome_source}


async def get_calibration(*, session, project_id: str) -> dict:
    """Return Brier score + stats for a project (all resolved verdicts)."""
    from sqlalchemy import text

    rows = (
        await session.execute(
            text("""
                SELECT classification, confidence, outcome
                FROM verdict_outcomes
                WHERE project_id = :pid
                  AND outcome != 'unresolved'
            """),
            {"pid": project_id},
        )
    ).mappings().all()

    stats = compute_brier_score([dict(r) for r in rows])
    return {"project_id": project_id, **stats}
