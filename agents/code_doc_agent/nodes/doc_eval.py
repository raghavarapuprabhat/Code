"""Phase 8b — DocEval (§8.9.3): golden-Q&A evaluation harness. SKIPPABLE.

After (re-)indexing, score the docs' ability to answer a golden question set. The set is
either supplied per project (future: stored config) or auto-derived from the generated
docs' section headings. For each question we retrieve context with the hybrid retriever
and ask an LLM judge whether the retrieved context can ground a correct answer
(0/1), plus a citation-presence check.

Output: `eval_results` = {score, total, passed, items:[{question, grounded, citation}]}.
Persisted to `doc_eval_runs` so the Hub can show a quality badge + trend.

Skips cleanly (score=None) when there are no docs or the LLM is unavailable.
"""
from __future__ import annotations

import json
import uuid

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState
from ..tools.json_tools import extract_json

logger = structlog.get_logger()

_MAX_QUESTIONS = 8


def _auto_questions(generated_docs: dict[str, str], model: dict) -> list[str]:
    """Derive a small golden set from component names + doc headings."""
    qs: list[str] = []
    for c in (model.get("components") or [])[:4]:
        name = c.get("name", "")
        if name:
            qs.append(f"What is the responsibility of the {name} component?")
    # A couple of structural questions that good docs should always answer.
    if model.get("datastores"):
        qs.append("What datastores does the system use?")
    if model.get("endpoints"):
        qs.append("Name one API endpoint and what it does.")
    if model.get("deployment_units"):
        qs.append("How is the system deployed?")
    if not qs:
        # Fall back to headings of the management overview.
        mgmt = generated_docs.get("01_management_overview", "")
        for line in mgmt.splitlines():
            if line.startswith("## "):
                qs.append(f"Explain: {line[3:].strip()}")
    return qs[:_MAX_QUESTIONS]


async def _judge(llm, question: str, context: str) -> dict:
    prompt = (
        "Given ONLY the context, can a correct, grounded answer to the question be given? "
        "Reply with a JSON object: {\"grounded\": true|false, \"has_citation\": true|false, "
        "\"answer\": \"one-sentence answer or 'insufficient context'\"}.\n\n"
        f"Question: {question}\n\nContext:\n{context[:6000]}"
    )
    try:
        resp = await llm.chat([{"role": "user", "content": prompt}])
        parsed = _safe_json(resp.content) or {}
        return {
            "grounded": bool(parsed.get("grounded")),
            "citation": bool(parsed.get("has_citation")),
            "answer": parsed.get("answer", ""),
        }
    except Exception:  # noqa: BLE001
        return {"grounded": False, "citation": False, "answer": "(judge failed)"}


async def _retrieve(pid: str, question: str) -> str:
    try:
        from shared.retrieval import hybrid_search
        from shared.storage import ChromaStore
        store = ChromaStore()
        hits = hybrid_search(store, f"docs_{pid}", question, n_results=5)
        return "\n\n".join(f"[{h['meta'].get('title','doc')}] {h['text']}" for h in hits)
    except Exception:  # noqa: BLE001
        return ""


async def run_doc_eval(*, project_id: str, generated_docs: dict, model: dict,
                       config: dict, questions: list[str] | None = None) -> dict:
    """Reusable eval routine — called by the node and the on-demand endpoint."""
    qs = questions or _auto_questions(generated_docs, model)
    if not qs:
        return {"score": None, "total": 0, "passed": 0, "items": [], "skipped": True}

    llm = build_adapter_from_config(config)
    items = []
    passed = 0
    for q in qs:
        ctx = await _retrieve(project_id, q)
        verdict = await _judge(llm, q, ctx)
        ok = verdict["grounded"]
        passed += int(ok)
        items.append({"question": q, "grounded": verdict["grounded"],
                      "citation": verdict["citation"], "answer": verdict["answer"]})

    total = len(qs)
    score = round(passed / total, 3) if total else None
    result = {"score": score, "total": total, "passed": passed, "items": items}
    await _persist_eval(project_id, result)
    return result


async def _persist_eval(pid: str, result: dict) -> None:
    try:
        from sqlalchemy import text
        from shared.storage import get_session, init_db, is_sqlite, portable_sql
        if is_sqlite():
            await init_db()
        async with get_session() as session:
            await session.execute(
                text(portable_sql("""
                    INSERT INTO doc_eval_runs (id, project_id, score, total, passed, detail_json)
                    VALUES (:id, :pid, :score, :total, :passed, :detail)
                """)),
                {"id": str(uuid.uuid4()), "pid": pid, "score": result.get("score"),
                 "total": result.get("total", 0), "passed": result.get("passed", 0),
                 "detail": json.dumps(result.get("items", []))},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_eval_persist_failed", err=str(exc))


async def doc_eval_node(state: CodeDocState, *, config: dict) -> dict:
    cfg = config.get("code_doc", {}) or {}
    if not cfg.get("doc_eval", True):
        return {"eval_results": {}}
    docs = state.get("generated_docs") or {}
    model = state.get("architecture_model") or {}
    if not docs:
        return {"eval_results": {}}
    result = await run_doc_eval(
        project_id=state["project_id"], generated_docs=docs, model=model, config=config,
    )
    logger.info("doc_eval_done", score=result.get("score"), passed=result.get("passed"),
                total=result.get("total"))
    return {"eval_results": result}


def _safe_json(text: str):
    return extract_json(text)
