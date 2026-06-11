"""Phase 4 — run the project's test suite via the whitelisted command."""
from __future__ import annotations

import structlog

from ..state import FixerState
from ..tools.test_runner import (
    TestRunnerSafetyError,
    detect_command_key,
    run_tests,
    tail,
)

logger = structlog.get_logger()


async def run_tests_node(state: FixerState, *, config: dict) -> dict:
    cfg = config["fixer"]
    plan = state.get("plan") or {}
    repo_path = state["repo_path"]

    key = plan.get("test_command_key") or detect_command_key(repo_path)
    if not key or key not in cfg["test_commands"]:
        msg = (
            f"No valid test_command_key (planner returned {plan.get('test_command_key')!r}, "
            f"detected {detect_command_key(repo_path)!r})."
        )
        logger.error("fixer_test_key_invalid", err=msg)
        return {
            "status": "raised_human",
            "error": msg,
            "audit_trail": (state.get("audit_trail") or []) + [
                {"step": "run_tests", "status": "no_command", "detail": msg}
            ],
        }

    command = list(cfg["test_commands"][key])
    timeout = int(cfg.get("test_timeout_seconds", 600))

    try:
        outcome = await run_tests(repo_path=repo_path, command=command, timeout_seconds=timeout)
    except TestRunnerSafetyError as e:
        return {
            "status": "raised_human",
            "error": f"Test runner blocked: {e}",
            "audit_trail": (state.get("audit_trail") or []) + [
                {"step": "run_tests", "status": "blocked", "detail": str(e)}
            ],
        }

    result = {
        "passed": outcome.passed,
        "command": outcome.command,
        "duration_ms": outcome.duration_ms,
        "stdout_tail": tail(outcome.stdout),
        "stderr_tail": tail(outcome.stderr),
        "failed_tests": outcome.failed_tests,
        "return_code": outcome.return_code,
    }
    test_history = list(state.get("test_history") or []) + [result]
    logger.info(
        "fixer_tests_done",
        passed=outcome.passed,
        duration_ms=outcome.duration_ms,
        failed=len(outcome.failed_tests),
    )
    return {
        "last_test": result,
        "test_history": test_history,
        "status": "tests_passed" if outcome.passed else "tests_failed",
        "audit_trail": (state.get("audit_trail") or []) + [
            {
                "step": "run_tests",
                "status": "ok" if outcome.passed else "failed",
                "duration_ms": outcome.duration_ms,
                "failed_tests": outcome.failed_tests[:10],
            }
        ],
    }
