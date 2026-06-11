"""Smoke tests for the agentic SRE investigator (foundation, §9.5).

Runnable without external services: Chroma is pointed at a temp persistent dir and
the LLM is mocked. Verifies (1) deterministic stack-trace parsing and (2) the full
graph wiring Understand → Ground → Hypothesize → Investigate → Conclude producing an
evidence-cited verdict with an investigation log.

Run:  python -m pytest agents/sre_agent/tests/test_smoke.py
  or: python agents/sre_agent/tests/test_smoke.py   (asserts inline)
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


# --- 1. stack-trace parsing -------------------------------------------------

def test_parse_java_stack():
    from agents.sre_agent.tools.stacktrace import parse_stack_trace

    trace = (
        "java.lang.NullPointerException: Cannot invoke getTotal()\n"
        "\tat com.example.OrderService.price(OrderService.java:142)\n"
        "\tat com.example.CheckoutController.checkout(CheckoutController.java:88)\n"
    )
    out = parse_stack_trace(trace)
    assert out["exception_type"] == "NullPointerException"
    assert len(out["frames"]) == 2
    top = out["frames"][0]
    assert top["relative_path"] == "OrderService.java"
    assert top["line"] == 142
    assert "OrderService" in out["error_signature"]


def test_parse_js_and_python():
    from agents.sre_agent.tools.stacktrace import parse_stack_trace

    js = (
        "TypeError: Cannot read properties of null (reading 'total')\n"
        "    at OrderService.price (/app/src/services/OrderService.ts:142:20)\n"
    )
    out = parse_stack_trace(js)
    assert out["exception_type"] == "TypeError"
    assert out["frames"][0]["relative_path"] == "OrderService.ts"
    assert out["frames"][0]["line"] == 142

    py = (
        "Traceback (most recent call last):\n"
        '  File "/app/order_service.py", line 142, in price\n'
        "    return order.total\n"
        "AttributeError: 'NoneType' object has no attribute 'total'\n"
    )
    outp = parse_stack_trace(py)
    assert outp["exception_type"] == "AttributeError"
    assert outp["frames"][0]["relative_path"] == "order_service.py"
    assert outp["frames"][0]["line"] == 142


# --- 2. end-to-end graph with a mocked LLM ----------------------------------

def _mock_chat_factory():
    """Return an async chat() that answers each node by inspecting the prompt.

    The investigate planner is stateful: turn 1 takes a tool action (no observation
    yet), turn 2 folds the observation into evidence + re-scores and stops confident
    — exercising the real reason→act→observe→reflect path.
    """
    from shared.llm_adapter.client import LLMResponse

    plan_calls = {"n": 0}

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        prompt = messages[-1]["content"]
        if "differential diagnosis" in prompt:
            body = json.dumps(
                {"hypotheses": [
                    {"id": "H1", "statement": "cache.get(id) null on miss; no guard", "prior": 0.5},
                    {"id": "H2", "statement": "bad input id from controller", "prior": 0.3},
                ]}
            )
        elif "ReAct loop" in prompt:
            plan_calls["n"] += 1
            if plan_calls["n"] == 1:
                # Turn 1: no observation yet — take the first action.
                body = json.dumps(
                    {
                        "thought": "read the failing line",
                        "action": "tool",
                        "tool": "fetch_code_snippet",
                        "args": {"file": "OrderService.java", "start_line": 138, "end_line": 150},
                    }
                )
            else:
                # Turn 2: interpret the observation, confirm H1, refute H2, stop.
                body = json.dumps(
                    {
                        "evidence": [
                            {"source": "code", "citation": "OrderService.java:142",
                             "finding": "order from cache.get(id), no null check", "bears_on": ["H1"],
                             "effect": "supports"},
                        ],
                        "hypothesis_updates": [
                            {"id": "H1", "posterior": 0.9, "status": "supported"},
                            {"id": "H2", "posterior": 0.1, "status": "refuted"},
                        ],
                        "thought": "confirmed the missing null guard",
                        "action": "stop",
                        "stop_reason": "confident",
                    }
                )
        elif "writing up the **conclusion**" in prompt or "writing up the" in prompt:
            body = json.dumps(
                {
                    "classification": "bug",
                    "confidence": 0.9,
                    "root_cause": "cache read without null guard NPEs on miss",
                    "rationale": "E1 shows no null check at OrderService.java:142",
                    "citations": ["OrderService.java:142"],
                    "likely_files": ["OrderService.java"],
                    "suggested_owner": None,
                    "next_step": "null-guard the cache miss",
                    "questions": [],
                }
            )
        else:  # intake
            body = json.dumps(
                {"title": "NPE in checkout", "description": "NullPointerException in checkout",
                 "stack_trace": "at com.example.OrderService.price(OrderService.java:142)",
                 "environment": "prod"}
            )
        return LLMResponse(content=body, tokens_in=0, tokens_out=0, model="mock")

    return chat


def test_graph_end_to_end():
    from shared.llm_adapter.client import LLMAdapter

    orig = LLMAdapter.chat
    LLMAdapter.chat = _mock_chat_factory()
    try:
        from agents.sre_agent.graph import run_triage

        result = asyncio.run(
            run_triage(
                project_id="nonexistent-test-project",
                user_message="NullPointerException in checkout, prod\n"
                "at com.example.OrderService.price(OrderService.java:142)",
                conversation_id="test-conv-1",
            )
        )
    finally:
        LLMAdapter.chat = orig

    facts = result.get("facts") or {}
    assert facts.get("exception_type") == "NullPointerException"
    verdict = result.get("verdict") or {}
    assert verdict.get("classification") == "bug"
    assert verdict.get("root_cause")
    assert "OrderService.java:142" in (verdict.get("citations") or [])
    assert result.get("investigation_log"), "expected a ReAct trace"
    assert result.get("handoff"), "bug over threshold should hand off to the Fixer"
    hp = result["handoff"]
    assert hp.get("root_cause") and "suspect_locations" in hp
    print("end-to-end OK:", verdict["classification"], verdict["confidence"],
          "| steps:", len(result["investigation_log"]),
          "| evidence:", len(result.get("evidence") or []))


if __name__ == "__main__":
    # Isolate side effects: temp Chroma dir + temp SQLite DB.
    tmp = tempfile.mkdtemp(prefix="sre_smoke_")
    os.environ["CHROMA_PATH"] = os.path.join(tmp, "chroma")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(tmp, 'smoke.db')}"
    test_parse_java_stack()
    test_parse_js_and_python()
    print("stack-trace parsing OK")
    test_graph_end_to_end()
    print("ALL SMOKE TESTS PASSED")
