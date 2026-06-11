"""Run project tests in a controlled subprocess with whitelist + timeout.

Only commands defined in the agent's config (`fixer.test_commands`) are accepted.
The LLM picks a *key* (e.g. "java_maven"), never an arbitrary command line.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass


class TestRunnerSafetyError(RuntimeError):
    pass


@dataclass
class RunOutcome:
    passed: bool
    command: list[str]
    duration_ms: int
    stdout: str
    stderr: str
    return_code: int
    failed_tests: list[str]


def detect_command_key(repo_path: str) -> str | None:
    """Pick a test-command key from the build files present in the repo root."""
    files = set(os.listdir(repo_path)) if os.path.isdir(repo_path) else set()
    if "pom.xml" in files:
        return "java_maven"
    if "build.gradle" in files or "build.gradle.kts" in files:
        return "java_gradle"
    if "package.json" in files:
        if "pnpm-lock.yaml" in files:
            return "node_pnpm"
        if "yarn.lock" in files:
            return "node_yarn"
        return "node_npm"
    if "pytest.ini" in files or "pyproject.toml" in files:
        return "python_pytest"
    return None


async def run_tests(
    *,
    repo_path: str,
    command: list[str],
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> RunOutcome:
    if not command or not isinstance(command, list):
        raise TestRunnerSafetyError("Empty or non-list test command rejected.")

    # Defense in depth: forbid shell metacharacters in any token.
    bad_chars = re.compile(r"[;&|`$><]")
    for token in command:
        if not isinstance(token, str) or bad_chars.search(token):
            raise TestRunnerSafetyError(f"Refusing test token: {token!r}")

    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return RunOutcome(
            passed=False,
            command=command,
            duration_ms=int((time.monotonic() - start) * 1000),
            stdout="",
            stderr=f"Test command timed out after {timeout_seconds}s",
            return_code=-1,
            failed_tests=[],
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    rc = proc.returncode or 0
    failed = _extract_failed_tests(stdout + "\n" + stderr)
    return RunOutcome(
        passed=(rc == 0 and not failed),
        command=command,
        duration_ms=duration_ms,
        stdout=stdout,
        stderr=stderr,
        return_code=rc,
        failed_tests=failed,
    )


_FAIL_PATTERNS = [
    re.compile(r"Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: \d+"),
    re.compile(r"FAIL ([\w./\\-]+\.test\.[jt]sx?)"),
    re.compile(r"FAILED ([\w./\\-]+::\w+)"),
    re.compile(r"^FAILED (.+)$", re.MULTILINE),
]


def _extract_failed_tests(output: str) -> list[str]:
    found: set[str] = set()
    for pat in _FAIL_PATTERNS:
        for m in pat.finditer(output):
            if m.groups():
                found.add(m.group(1))
    return sorted(found)


def tail(text: str, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    return "...[truncated]...\n" + text[-max_chars:]
