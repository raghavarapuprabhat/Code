"""Tests for robust LLM-JSON extraction (agents/code_doc_agent/tools/json_tools.py).

Covers the real failure modes seen with Claude/Sonnet behind an OpenAI-compatible
gateway that does not enforce response_format: prose preambles, markdown fences,
trailing commentary, braces inside prose and inside strings, arrays, and the
must-return-None cases (truncated / no JSON).

Run:  python agents/code_doc_agent/tests/test_json_tools.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_extract_json_handles_sonnet_wrapping():
    from agents.code_doc_agent.tools.json_tools import extract_json as e

    # Plain + fenced
    assert e('{"a": 1}') == {"a": 1}
    assert e('```json\n{"a": 1}\n```') == {"a": 1}
    assert e('```\n{"a": 1}\n```') == {"a": 1}

    # Sonnet preamble + fenced payload (the most common real failure)
    assert e('Here is the summary:\n```json\n{"purpose": "x", "business_rules": []}\n```') == {
        "purpose": "x",
        "business_rules": [],
    }

    # Trailing commentary after the JSON
    assert e('{"a": 3}\n\nLet me know if you need more detail.') == {"a": 3}

    # A non-JSON brace group in prose must not hide the real JSON that follows
    assert e("The handler (see {note}) does this. JSON:\n{\"a\": 2}") == {"a": 2}

    # Braces inside string values must not break depth counting
    assert e('{"desc": "rejects when total > 0 } edge", "ok": true}')["ok"] is True

    # Arrays are valid top-level JSON
    assert e('[{"x": 1}, {"y": 2}]') == [{"x": 1}, {"y": 2}]

    # Junk block before the fenced real answer
    assert e('first {junk: 0} then:\n```json\n{"real": true}\n```') == {"real": True}

    print("extract_json handles Sonnet wrapping OK")


def test_extract_json_returns_none_when_unusable():
    from agents.code_doc_agent.tools.json_tools import extract_json as e

    assert e("") is None
    assert e(None) is None
    assert e("I cannot help with that.") is None
    assert e('{"a": 1, "b":') is None  # truncated / unbalanced
    print("extract_json returns None on unusable input OK")


def test_nodes_delegate_to_extractor():
    """Every LLM node's local _safe_json must route through the robust extractor."""
    from agents.code_doc_agent.nodes.semantic_pass import _safe_json as sp
    from agents.code_doc_agent.nodes.cross_file import _safe_json as cf
    from agents.code_doc_agent.nodes.api_surface import _safe_json as ap
    from agents.code_doc_agent.nodes.batch_jobs import _safe_json as bj

    wrapped = 'Sure!\n```json\n{"k": 1}\n```'
    for fn in (sp, cf, ap, bj):
        assert fn(wrapped) == {"k": 1}, fn
    print("node _safe_json delegation OK")


if __name__ == "__main__":
    test_extract_json_handles_sonnet_wrapping()
    test_extract_json_returns_none_when_unusable()
    test_nodes_delegate_to_extractor()
    print("\nALL JSON-TOOLS TESTS PASSED")
