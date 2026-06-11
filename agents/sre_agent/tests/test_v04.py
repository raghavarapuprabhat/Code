"""v0.4 tests — runtime probe rails + mid-loop ask_user (interrupt/resume).

No external services: Chroma → temp dir, LLM mocked, probes hit a tiny local sqlite.
Run:  python agents/sre_agent/tests/test_v04.py
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


# --- probe rails ------------------------------------------------------------

def test_sql_guard_and_db_probe():
    from shared.probes import validate_select_sql, db_probe

    assert validate_select_sql("SELECT 1", dialect="sqlite").ok
    assert not validate_select_sql("UPDATE t SET a=1", dialect="sqlite").ok
    assert not validate_select_sql("SELECT 1; DROP TABLE t", dialect="sqlite").ok

    os.environ["PROBE_TEST_DSN"] = "sqlite+aiosqlite:///" + os.path.abspath("aiagent.db")
    target = {"kind": "db", "name": "app", "environment": "dev",
              "base_url_or_dsn_ref": "PROBE_TEST_DSN"}
    res = asyncio.run(db_probe(target, "SELECT name FROM sqlite_master WHERE type='table'"))
    assert res.ok and res.detail["rowcount"] >= 1
    # A write must be rejected before touching the DB.
    bad = asyncio.run(db_probe(target, "DELETE FROM code_projects"))
    assert not bad.ok and "rejected" in bad.error.lower()
    print("probe rails OK:", res.summary)


def test_http_host_confinement():
    from shared.probes import http_probe
    os.environ["PROBE_BASE"] = "http://127.0.0.1:9"  # unroutable; we only test confinement
    target = {"kind": "http", "name": "x", "environment": "dev", "base_url_or_dsn_ref": "PROBE_BASE"}
    # Absolute URL in path → rejected (prompt-injection guard).
    res = asyncio.run(http_probe(target, "GET", "http://evil.com/steal"))
    assert not res.ok and "host confinement" in res.error
    # Write method rejected.
    res2 = asyncio.run(http_probe(target, "POST", "/orders"))
    assert not res2.ok and "not allowed" in res2.error
    print("http confinement OK")


# --- interrupt / resume -----------------------------------------------------

def _mock_chat():
    from shared.llm_adapter.client import LLMResponse
    plan = {"n": 0}

    async def chat(self, messages, **kw):  # noqa: ANN001
        p = messages[-1]["content"]
        if "differential diagnosis" in p:
            b = json.dumps({"hypotheses": [
                {"id": "H1", "statement": "cache miss → null", "prior": 0.5}]})
        elif "ReAct loop" in p:
            plan["n"] += 1
            if plan["n"] == 1:
                b = json.dumps({"thought": "need to read prod data",
                                "action": "ask_user", "blocks": "probe_approval",
                                "question": "Approve read-only PROD db probe?",
                                "options": ["Approve", "Skip"]})
            else:
                b = json.dumps({
                    "evidence": [{"source": "user", "citation": "prod approval",
                                  "finding": "approved", "bears_on": ["H1"], "effect": "supports"}],
                    "hypothesis_updates": [{"id": "H1", "posterior": 0.9, "status": "supported"}],
                    "thought": "confirmed", "action": "stop", "stop_reason": "confident"})
        elif "writing up" in p:
            b = json.dumps({"classification": "bug", "confidence": 0.9,
                            "root_cause": "cache miss null", "rationale": "approved + supported",
                            "citations": ["OrderService.java:142"], "questions": []})
        else:
            b = json.dumps({"title": "npe", "description": "NPE", "environment": "prod",
                            "stack_trace": "at A.b(OrderService.java:142)"})
        return LLMResponse(content=b, tokens_in=0, tokens_out=0, model="mock")
    return chat


def test_interrupt_resume():
    from shared.llm_adapter.client import LLMAdapter
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command
    from agents.sre_agent.graph import build_graph, load_config

    orig = LLMAdapter.chat
    LLMAdapter.chat = _mock_chat()
    try:
        app = build_graph(load_config(), checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "tA"}}
        r1 = asyncio.run(app.ainvoke(
            {"project_id": "p", "user_message": "NPE prod", "conversation_id": "tA",
             "allow_interrupt": True}, cfg))
        assert r1.get("__interrupt__"), "expected a pause for prod approval"
        q = r1["__interrupt__"][0].value
        assert q["blocks"] == "probe_approval"
        # Resume with approval.
        r2 = asyncio.run(app.ainvoke(Command(resume="Approve"), cfg))
        v = r2.get("verdict") or {}
        assert v.get("classification") == "bug", v
        assert any(e.get("source") == "user" for e in r2.get("evidence") or []), "approval recorded"
        assert r2.get("prod_probe_approved") is True
    finally:
        LLMAdapter.chat = orig
    print("interrupt/resume OK:", v["classification"], v["confidence"])


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="sre_v04_")
    os.environ["CHROMA_PATH"] = os.path.join(tmp, "chroma")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///" + os.path.abspath("aiagent.db"))
    test_sql_guard_and_db_probe()
    test_http_host_confinement()
    test_interrupt_resume()
    print("ALL v0.4 TESTS PASSED")
