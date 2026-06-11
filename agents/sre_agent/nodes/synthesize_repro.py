"""Phase 4.5 — synthesize a failing repro test (§9.17.3).

After Conclude produces a high-confidence bug verdict this node:
1.  Drafts a minimal failing unit test via LLM that encodes the root cause.
2.  Writes the test to a temp path inside the repo and runs it (pytest only for now;
    other frameworks get status="unverified" so the Fixer writes+runs it instead).
3.  Confirms the test is RED: exits non-zero AND the expected failure pattern is present.
4.  Cleans up the temp file regardless of outcome.
5.  Returns ``repro_test`` in state: {path, content, status, failure_excerpt}.

``status`` values:
  "red"         — confirmed failing for the expected reason → Fixer is test-first
  "unverified"  — content drafted but not run (no runner / wrong framework)
  "skip"        — synthesis failed; Fixer proceeds without a repro test
"""
from __future__ import annotations

import json
import os
import re
import tempfile

import structlog
from sqlalchemy import text

from shared.llm_adapter import build_adapter_from_config
from shared.storage import get_session
from ..state import SREState

# Reuse the Fixer's test-runner utilities (safe subprocess wrapper).
from agents.sre_fixer_agent.tools.test_runner import (
    RunOutcome,
    TestRunnerSafetyError,
    detect_command_key,
    run_tests,
)

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "synthesize_repro.md")
_MAX_FILE_BYTES = 12_000
_PYTEST_TIMEOUT = 60


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


async def _repo_path(project_id: str) -> str | None:
    """Look up the project's checked-out repo path from code_projects."""
    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT project_path FROM code_projects WHERE id = :id"),
                    {"id": project_id},
                )
            ).first()
        return row.project_path if row else None
    except Exception:  # noqa: BLE001
        return None


def _read_file(repo_path: str, rel: str) -> str:
    for candidate in [rel, os.path.basename(rel)]:
        full = os.path.join(repo_path, candidate)
        if os.path.isfile(full):
            try:
                with open(full, errors="replace") as fh:
                    return fh.read()[:_MAX_FILE_BYTES]
            except OSError:
                pass
    return ""


def _test_conventions(repo_path: str) -> str:
    """Return a short sample from the first test file found (naming conventions)."""
    for root, _, files in os.walk(repo_path):
        for fn in files:
            if fn.startswith("test_") and fn.endswith(".py"):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, repo_path)
                try:
                    with open(full, errors="replace") as fh:
                        sample = fh.read(800)
                    return f"# {rel}\n{sample}"
                except OSError:
                    pass
    return "(no existing test files found)"


async def _run_pytest_targeted(repo_path: str, test_path: str) -> RunOutcome | None:
    """Run just the one repro test file; return None if pytest not available."""
    key = detect_command_key(repo_path)
    if key != "python_pytest":
        return None
    command = ["python", "-m", "pytest", "-x", "--tb=short", test_path]
    try:
        return await run_tests(
            repo_path=repo_path,
            command=command,
            timeout_seconds=_PYTEST_TIMEOUT,
        )
    except (TestRunnerSafetyError, Exception):  # noqa: BLE001
        return None


def _safe_json(text: str):
    text = (text or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None


async def synthesize_repro_node(state: SREState, *, config: dict) -> dict:
    sre_cfg = config.get("sre", {}) or {}
    enabled = (sre_cfg.get("handoff", {}) or {}).get("synthesize_repro_test", True)
    if not enabled:
        logger.info("synthesize_repro_skipped", reason="config disabled")
        return {"repro_test": None}

    project_id = state.get("project_id", "")
    verdict = state.get("verdict") or {}
    issue = state.get("issue") or {}
    evidence = state.get("evidence") or []
    rag_hits = state.get("rag_hits") or []

    llm = build_adapter_from_config(config)
    template = _load_prompt()

    # Try to locate the repo so we can run the test to confirm it's red.
    repo_path = await _repo_path(project_id)

    # Build files block from likely_files.
    likely = verdict.get("likely_files") or []
    files_parts = []
    if repo_path:
        for rel in likely[:5]:
            content = _read_file(repo_path, rel)
            if content:
                files_parts.append(f"### {rel}\n```\n{content}\n```")
    if not files_parts:
        files_parts.append("(source files not accessible at synthesis time)")
    files_block = "\n\n".join(files_parts)

    test_conventions = _test_conventions(repo_path) if repo_path else "(repo not accessible)"

    verdict_block = json.dumps({
        "issue": {"title": issue.get("title"), "description": (issue.get("description") or "")[:400]},
        "verdict": {
            "classification": verdict.get("classification"),
            "root_cause": verdict.get("root_cause"),
            "rationale": verdict.get("rationale"),
            "likely_files": likely,
            "citations": verdict.get("citations", [])[:6],
        },
        "evidence": [
            {"source": e.get("source"), "citation": e.get("citation"), "finding": e.get("finding", "")[:200]}
            for e in evidence[:8]
        ],
    }, indent=2)

    prompt = (
        template
        .replace("{verdict_json}", verdict_block)
        .replace("{files_block}", files_block)
        .replace("{test_conventions}", test_conventions)
    )

    resp = await llm.chat([{"role": "user", "content": prompt}])
    draft = _safe_json(resp.content)

    if not draft or not draft.get("test_content") or not draft.get("test_file_path"):
        logger.warning("synthesize_repro_unparseable", raw=str(resp.content)[:300])
        return {"repro_test": {"path": None, "content": None, "status": "skip", "failure_excerpt": ""}}

    rel_path: str = draft["test_file_path"].lstrip("/")
    content: str = draft["test_content"]
    expected_pattern: str = draft.get("expected_failure_pattern", "")

    repro_test: dict = {
        "path": rel_path,
        "content": content,
        "status": "unverified",
        "failure_excerpt": "",
        "expected_failure_pattern": expected_pattern,
        "rationale": draft.get("rationale", ""),
    }

    # Attempt to run the test to confirm it's red.
    if repo_path:
        abs_test_path = os.path.join(repo_path, rel_path)
        os.makedirs(os.path.dirname(abs_test_path), exist_ok=True)
        wrote = False
        try:
            with open(abs_test_path, "w") as fh:
                fh.write(content)
            wrote = True

            outcome = await _run_pytest_targeted(repo_path, abs_test_path)
            if outcome is not None:
                combined = (outcome.stdout + "\n" + outcome.stderr)[-3000:]
                if not outcome.passed and (
                    not expected_pattern or re.search(expected_pattern, combined, re.IGNORECASE)
                ):
                    repro_test["status"] = "red"
                    repro_test["failure_excerpt"] = combined[-1200:]
                    logger.info("synthesize_repro_red", path=rel_path)
                elif outcome.passed:
                    # Test passes already → the fix may already be in? Or the test is wrong.
                    repro_test["status"] = "skip"
                    repro_test["failure_excerpt"] = "(test passed unexpectedly — skipping test-first)"
                    logger.warning("synthesize_repro_passed_unexpectedly", path=rel_path)
                else:
                    # Ran, failed, but wrong pattern — still include but mark unverified.
                    repro_test["failure_excerpt"] = combined[-600:]
                    logger.warning("synthesize_repro_wrong_pattern", path=rel_path)
        except OSError as exc:
            logger.warning("synthesize_repro_write_error", err=str(exc))
        finally:
            if wrote and os.path.exists(abs_test_path):
                try:
                    os.remove(abs_test_path)
                except OSError:
                    pass

    logger.info("synthesize_repro_done", status=repro_test["status"], path=rel_path)
    return {"repro_test": repro_test}
