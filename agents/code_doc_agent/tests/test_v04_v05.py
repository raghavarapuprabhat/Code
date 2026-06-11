"""Code Doc Agent v0.4/v0.5 tests — deterministic pieces + end-to-end graph (mocked LLM).

No external services: Chroma → temp dir, DB → temp sqlite, LLM mocked, git optional.
Run:  python agents/code_doc_agent/tests/test_v04_v05.py
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


# --- ConfigInfraScan --------------------------------------------------------

def test_config_infra_scan():
    from agents.code_doc_agent.nodes.config_infra import (
        _parse_package_json, _scan_config_text, _classify_dsn,
    )
    pkg = _parse_package_json('{"name":"app","dependencies":{"react":"18.0.0"},"scripts":{"build":"vite"}}')
    assert "react" in pkg["dependencies"]

    ds, ext = _scan_config_text("spring.datasource.url=jdbc:postgresql://db:5432/app\nPAYMENT_API_URL=https://pay.example.com")
    assert any(d["kind"] == "postgres" for d in ds), ds
    assert ext, ext
    # Value must be masked.
    assert "«value»" in ds[0]["masked"]
    assert "5432" not in ds[0]["masked"]
    print("config_infra OK:", [d["kind"] for d in ds])


# --- ArchSynthesis ----------------------------------------------------------

def test_arch_synthesis_contract():
    """Verify the model dict has the exact keys the SRE tools read."""
    from agents.code_doc_agent.nodes.arch_synthesis import _resolve_connectors, _map_datastores

    asts = {
        "web/Ctrl.java": {"classes": [{"name": "Ctrl", "annotations": [{"name": "@RestController"}], "methods": []}], "imports": ["svc.Svc"]},
        "svc/Svc.java": {"classes": [{"name": "Svc", "annotations": [{"name": "@Service"}], "methods": []}], "imports": []},
    }
    conns = _resolve_connectors(asts, {"web/Ctrl.java": "Web", "svc/Svc.java": "Svc"})
    assert conns and all(k in conns[0] for k in ("from", "to", "kind", "evidence"))

    ci = {"datasources": [{"kind": "postgres", "dsn_env": "DATABASE_URL", "discovered_from": "application.yml"}]}
    ds = _map_datastores(ci, asts)
    assert ds and all(k in ds[0] for k in ("kind", "entities", "dsn_env", "discovered_from"))
    print("arch_synthesis contract OK")


# --- QualityScan ------------------------------------------------------------

def test_quality_hotspots():
    from agents.code_doc_agent.nodes.quality_scan import _hotspots, _complexity

    asts = {"a.java": {"classes": [{"name": "A", "methods": [{"start_line": 1, "end_line": 40}]}]}}
    inv = [{"relative_path": "a.java", "loc": 100}]
    churn = {"a.java": 10}
    hs = _hotspots(asts, inv, churn)
    assert hs and hs[0]["file"] == "a.java" and hs[0]["score"] == 1.0
    print("quality hotspots OK:", hs[0])


# --- Hybrid retrieval (BM25 + RRF) ------------------------------------------

def test_bm25_and_rrf():
    from shared.retrieval import BM25

    bm = BM25([["cache", "miss", "null"], ["payment", "gateway"], ["cache", "aside"]])
    scores = bm.scores("cache null")
    # doc 0 (cache+null) should outscore doc 1 (payment).
    assert scores[0] > scores[1]
    print("bm25 OK:", [round(s, 2) for s in scores])


def test_rrf_fusion_offline():
    """RRF fuses two rankings without a real Chroma store (uses a fake store)."""
    from shared.retrieval import hybrid_search, invalidate_corpus_cache

    class FakeCol:
        def __init__(self):
            self._docs = ["cache miss null pointer", "payment gateway timeout", "cache aside pattern"]
            self._ids = ["d0", "d1", "d2"]

        def get(self):
            return {"ids": self._ids, "documents": self._docs, "metadatas": [{}, {}, {}]}

        def query(self, query_texts, n_results, where=None):
            # Pretend vector search ranks d2 first, then d0, then d1.
            order = ["d2", "d0", "d1"][:n_results]
            idx = {"d0": 0, "d1": 1, "d2": 2}
            return {
                "ids": [order],
                "documents": [[self._docs[idx[i]] for i in order]],
                "metadatas": [[{} for _ in order]],
            }

    class FakeStore:
        def __init__(self):
            self.col = FakeCol()

        def get_or_create_collection(self, name):
            return self.col

        def query(self, collection, *, query_texts, n_results, where=None):
            return self.col.query(query_texts, n_results, where)

    invalidate_corpus_cache()
    hits = hybrid_search(FakeStore(), "docs_test", "cache null", n_results=3)
    assert hits, hits
    # d0 (lexical match on cache+null) should be fused high.
    ids = [h["id"] for h in hits]
    assert "d0" in ids
    print("rrf fusion OK:", ids)


# --- v0.5 doc renderers degrade gracefully ----------------------------------

def test_v05_docs_not_configured():
    from agents.code_doc_agent.tools.v05_docs import (
        render_requirements, render_dependencies, render_change_digest,
    )
    assert "Not configured" in render_requirements([], {}, None)
    assert "Not configured" in render_dependencies({})
    assert "first index" in render_change_digest("").lower()
    print("v05 docs graceful OK")


# --- Drift digest diff ------------------------------------------------------

def test_drift_diff():
    from agents.code_doc_agent.nodes.drift_digest import _diff_models

    prev = {"components": [{"name": "A"}], "connectors": [], "endpoints": [], "external_systems": [], "layers": [], "quality": {}}
    curr = {"components": [{"name": "A"}, {"name": "B"}], "connectors": [], "endpoints": [{"method": "GET", "path": "/x"}], "external_systems": [], "layers": [], "quality": {}}
    lines = _diff_models(prev, curr)
    assert any("New components" in l for l in lines)
    assert any("New endpoints" in l for l in lines)
    print("drift diff OK:", lines)


# --- End-to-end graph with mocked LLM --------------------------------------

def _mock_chat():
    from shared.llm_adapter.client import LLMResponse

    async def chat(self, messages, **kw):  # noqa: ANN001
        p = messages[-1]["content"]
        if "naming and describing" in p:           # arch_synthesis
            b = json.dumps({"components": [{"cluster_id": "C0", "name": "App", "layer": "service", "description": "core"}]})
        elif "inferring Architecture Decision" in p:  # ADR
            b = json.dumps({"decisions": [{"title": "SQLite default", "decision": "use sqlite", "evidence": ["config"], "confidence": "medium"}]})
        elif "quality judge" in p:                  # doc_critique
            b = json.dumps({"scores": {"groundedness": 5, "diagram_validity": 5, "audience_fit": 5, "consistency": 5, "coverage": 5}, "failing_criteria": [], "notes": "ok"})
        elif "can a correct, grounded answer" in p:  # doc_eval judge
            b = json.dumps({"grounded": True, "has_citation": True, "answer": "yes"})
        elif "modules" in p.lower() and "flows" in p.lower():  # cross_file
            b = json.dumps({"modules": [{"name": "app", "files": ["a.java"]}], "flows": [], "data_entities": [], "entry_points": []})
        elif "management" in p.lower():             # mgmt overview
            b = "# Overview\nThis app does things."
        else:                                        # semantic_pass / file summary
            b = json.dumps({"purpose": "x", "business_rules": [], "dependencies": [], "edge_cases": []})
        return LLMResponse(content=b, tokens_in=0, tokens_out=0, model="mock")
    return chat


def test_end_to_end_index():
    from shared.llm_adapter.client import LLMAdapter
    from agents.code_doc_agent.graph import run_indexing

    # Build a tiny fixture project.
    proj = tempfile.mkdtemp(prefix="cd_proj_")
    os.makedirs(os.path.join(proj, "src"), exist_ok=True)
    with open(os.path.join(proj, "src", "App.java"), "w") as fh:
        fh.write("@Service\npublic class App {\n  public void run() {}\n}\n")
    with open(os.path.join(proj, "application.yml"), "w") as fh:
        fh.write("spring.datasource.url: jdbc:postgresql://db/app\n")

    orig = LLMAdapter.chat
    LLMAdapter.chat = _mock_chat()
    try:
        result = asyncio.run(run_indexing(project_path=proj, mode="full", display_name="Fixture"))
        assert result["project_id"], result
        docs = result["docs_generated"]
        # v0.4 + v0.5 docs should all be present.
        for d in ("02_architecture", "09_deployment_infra", "10_architecture_decisions",
                  "11_quality_hotspots", "12_external_integrations", "13_dependencies",
                  "14_onboarding", "15_requirements_traceability", "16_change_digest"):
            assert d in docs, f"missing doc {d}; got {docs}"
        assert result["model_hash"], "model_hash should be set"
        print("end-to-end OK: docs=", len(docs), "components=", result["architecture_components"])
    finally:
        LLMAdapter.chat = orig


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="cd_v04_")
    os.environ["CHROMA_PATH"] = os.path.join(tmp, "chroma")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///" + os.path.join(tmp, "test.db"))

    test_config_infra_scan()
    test_arch_synthesis_contract()
    test_quality_hotspots()
    test_bm25_and_rrf()
    test_rrf_fusion_offline()
    test_v05_docs_not_configured()
    test_drift_diff()
    test_end_to_end_index()
    print("\nALL v0.4/v0.5 CODE-DOC TESTS PASSED")
