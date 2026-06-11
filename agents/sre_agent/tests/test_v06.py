"""v0.6 tests — steering, repro synthesis, calibration, severity, ADO write-back.

No external services required.
Run:  python agents/sre_agent/tests/test_v06.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# v0.6.8 — hypothesis steering (inject / kill / pin)
# ---------------------------------------------------------------------------

def test_steer_inject():
    from agents.sre_agent.nodes.investigate import _apply_steering

    hyps = [{"id": "H1", "statement": "cache miss", "posterior": 0.4, "status": "open"}]
    log: list = []
    _apply_steering(
        [{"action": "inject", "id": "Hu1", "statement": "race condition in lock"}],
        hyps, log,
    )
    assert len(hyps) == 2
    injected = next(h for h in hyps if h["id"] == "Hu1")
    assert injected["source"] == "user"
    assert injected["posterior"] == 0.5
    assert any(s.get("action") == "steer:inject" for s in log)
    print("steer inject OK:", injected["statement"])


def test_steer_kill():
    from agents.sre_agent.nodes.investigate import _apply_steering

    hyps = [{"id": "H1", "statement": "cache miss", "posterior": 0.8, "status": "open"}]
    log: list = []
    _apply_steering([{"action": "kill", "id": "H1", "statement": None}], hyps, log)
    assert hyps[0]["status"] == "refuted"
    assert hyps[0]["posterior"] <= 0.1
    assert any(s.get("action") == "steer:kill" for s in log)
    print("steer kill OK: status=", hyps[0]["status"])


def test_steer_pin():
    from agents.sre_agent.nodes.investigate import _apply_steering

    hyps = [{"id": "H1", "statement": "cache miss", "posterior": 0.4, "status": "open"}]
    log: list = []
    _apply_steering([{"action": "pin", "id": "H1", "statement": None}], hyps, log)
    assert hyps[0].get("pinned") is True
    assert any(s.get("action") == "steer:pin" for s in log)
    print("steer pin OK")


def test_steer_unknown_id_is_noop():
    from agents.sre_agent.nodes.investigate import _apply_steering

    hyps = [{"id": "H1", "statement": "cache miss", "posterior": 0.4, "status": "open"}]
    log: list = []
    _apply_steering([{"action": "kill", "id": "H99", "statement": None}], hyps, log)
    assert hyps[0]["status"] == "open"   # unchanged
    print("steer unknown id noop OK")


# ---------------------------------------------------------------------------
# v0.6.5 — Brier score calibration
# ---------------------------------------------------------------------------

def test_brier_score_perfect():
    from agents.sre_agent.calibration import compute_brier_score

    rows = [
        {"classification": "bug", "confidence": 1.0, "outcome": "confirmed"},
        {"classification": "bug", "confidence": 1.0, "outcome": "confirmed"},
        {"classification": "not_a_bug", "confidence": 1.0, "outcome": "overturned"},
    ]
    stats = compute_brier_score(rows)
    assert stats["n"] == 3
    assert stats["brier_score"] == 0.0, stats
    assert stats["accuracy"] == 1.0
    print("brier perfect OK:", stats)


def test_brier_score_worst():
    from agents.sre_agent.calibration import compute_brier_score

    # Model predicted bug with conf=1 but outcome was overturned (wrong every time).
    rows = [
        {"classification": "bug", "confidence": 1.0, "outcome": "overturned"},
        {"classification": "bug", "confidence": 1.0, "outcome": "overturned"},
    ]
    stats = compute_brier_score(rows)
    assert stats["brier_score"] == 1.0, stats
    assert stats["accuracy"] == 0.0
    print("brier worst OK:", stats)


def test_brier_skips_unresolved():
    from agents.sre_agent.calibration import compute_brier_score

    rows = [
        {"classification": "bug", "confidence": 0.8, "outcome": "confirmed"},
        {"classification": "bug", "confidence": 0.8, "outcome": "unresolved"},
    ]
    stats = compute_brier_score(rows)
    assert stats["n"] == 1
    assert stats["skipped"] == 1
    print("brier unresolved skip OK:", stats)


# ---------------------------------------------------------------------------
# v0.6.6 — severity / blast-radius hotspot score
# ---------------------------------------------------------------------------

def test_hotspot_score_no_evidence():
    from agents.sre_agent.nodes.severity import _hotspot_score

    assert _hotspot_score([], {}) == 0.0
    print("hotspot empty OK")


def test_hotspot_score_with_evidence():
    from agents.sre_agent.nodes.severity import _hotspot_score

    evidence = [
        {"source": "code", "citation": "OrderService.java:42", "finding": "NPE"},
        {"source": "code", "citation": "OrderService.java:80", "finding": "null check"},
        {"source": "git", "citation": "abc1234", "finding": "recent change"},
    ]
    score = _hotspot_score(evidence, {})
    assert 0 < score <= 1.0
    print("hotspot score OK:", score)


def test_severity_level_rules():
    from agents.sre_agent.nodes.severity import _level

    # High hotspot + payment endpoint → critical.
    level = _level(0.5, ["/api/v1/payment/process"], ["checkout flow"])
    assert level == "critical", level

    # Low hotspot, no critical endpoints → low.
    level_low = _level(0.1, [], [])
    assert level_low == "low", level_low
    print("severity level rules OK")


# ---------------------------------------------------------------------------
# v0.6.7 — ADO write-back (dry-run + consent gate)
# ---------------------------------------------------------------------------

def test_ado_writeback_dry_run():
    config = {"sre": {"ado_writeback": {"enabled": False, "tag": "sre-test"}}}
    verdict = {"root_cause": "NPE in checkout", "likely_files": ["OrderService.java"],
               "classification": "bug", "confidence": 0.9}
    issue = {"title": "NPE in checkout", "repro_steps": "1. add item 2. checkout"}
    result = asyncio.run(
        _ado_file_bug(
            conversation_id="test-conv", project_id="proj1",
            verdict=verdict, issue=issue, evidence=[], severity={"level": "high"},
            config=config, dry_run=True,
        )
    )
    assert result["dry_run"] is True
    assert result["filed"] is False
    print("ado dry run OK:", result["message"])


def test_ado_writeback_disabled_gate():
    config = {"sre": {"ado_writeback": {"enabled": False, "tag": "sre-test"}}}
    result = asyncio.run(
        _ado_file_bug(
            conversation_id="test-conv", project_id="proj1",
            verdict={}, issue={}, evidence=[], severity={},
            config=config, dry_run=False,  # enabled=False should also dry-run
        )
    )
    assert result["filed"] is False
    assert result["dry_run"] is True
    print("ado gate disabled OK:", result["message"])


async def _ado_file_bug(**kwargs):
    from agents.sre_agent.ado_writeback import file_bug
    return await file_bug(**kwargs)


# ---------------------------------------------------------------------------
# v0.6.3 — repro_test field flows through handoff
# ---------------------------------------------------------------------------

def test_handoff_includes_repro_test():
    """Ensure handoff_fixer_node forwards repro_test from state."""
    from agents.sre_agent.nodes.decide import handoff_fixer_node
    from agents.sre_agent.state import SREState

    state: SREState = {  # type: ignore[assignment]
        "project_id": "p1",
        "issue": {"title": "NPE"},
        "verdict": {"classification": "bug", "confidence": 0.9, "root_cause": "NPE",
                    "likely_files": [], "citations": []},
        "evidence": [],
        "rag_hits": [],
        "conversation_id": "conv1",
        "repro_test": {"path": "tests/test_repro.py", "content": "def test_npe(): ...",
                       "status": "red", "failure_excerpt": "AssertionError"},
    }
    result = asyncio.run(handoff_fixer_node(state, config={}))
    payload = result.get("handoff") or {}
    assert payload.get("repro_test") is not None
    assert payload["repro_test"]["status"] == "red"
    print("handoff repro_test OK:", payload["repro_test"]["path"])


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="sre_v06_")
    os.environ["CHROMA_PATH"] = os.path.join(tmp, "chroma")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///" + os.path.abspath("aiagent.db"))

    test_steer_inject()
    test_steer_kill()
    test_steer_pin()
    test_steer_unknown_id_is_noop()

    test_brier_score_perfect()
    test_brier_score_worst()
    test_brier_skips_unresolved()

    test_hotspot_score_no_evidence()
    test_hotspot_score_with_evidence()
    test_severity_level_rules()

    test_ado_writeback_dry_run()
    test_ado_writeback_disabled_gate()

    test_handoff_includes_repro_test()

    print("\nALL v0.6 TESTS PASSED")
