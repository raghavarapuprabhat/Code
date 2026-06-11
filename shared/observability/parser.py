"""Log parsing shared by query_logs (file adapter) and ingest_user_logs (§9.17.1).

Deterministic, no-LLM: extract timestamps, error lines, error frequencies, and group
by correlation/trace id. The output is a compact, citation-shaped summary the loop can
record as Evidence(source="logs"). User-supplied logs are untrusted content (§17): they
can support/refute hypotheses but can never name a probe target, host, or command.
"""
from __future__ import annotations

import re
from collections import Counter

_TS = re.compile(
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_LEVEL = re.compile(r"\b(ERROR|FATAL|SEVERE|WARN|WARNING|Exception|Error)\b")
_CORR = re.compile(r"(?:trace[_-]?id|correlation[_-]?id|request[_-]?id|x-request-id)[=:\s]+([\w-]{6,})", re.I)
_EXC = re.compile(r"\b([A-Z][\w.]*(?:Exception|Error))\b")


def parse_logs(text: str, *, query: str | None = None, max_lines: int = 4000) -> dict:
    if not text or not text.strip():
        return {"summary": "(no log content)", "first_ts": None, "error_count": 0, "lines": 0}

    lines = text.splitlines()[:max_lines]
    if query:
        ql = query.lower()
        filtered = [ln for ln in lines if ql in ln.lower()]
        lines = filtered or lines

    timestamps = [m.group(1) for ln in lines for m in [_TS.search(ln)] if m]
    error_lines = [ln for ln in lines if _LEVEL.search(ln)]
    exc_counts = Counter(m.group(1) for ln in error_lines for m in [_EXC.search(ln)] if m)
    corr_counts = Counter(m.group(1) for ln in lines for m in [_CORR.search(ln)] if m)

    first_ts = timestamps[0] if timestamps else None
    last_ts = timestamps[-1] if timestamps else None

    out = [
        f"{len(lines)} log line(s); {len(error_lines)} error/warn line(s).",
        f"time range: {first_ts or '?'} → {last_ts or '?'}.",
    ]
    if exc_counts:
        out.append("top errors: " + ", ".join(f"{k}×{v}" for k, v in exc_counts.most_common(5)))
    if corr_counts:
        out.append("correlation ids: " + ", ".join(f"{k}({v})" for k, v in corr_counts.most_common(3)))
    # A few representative error lines (truncated) so the loop has concrete text.
    for ln in error_lines[:5]:
        out.append("  | " + ln.strip()[:160])

    return {
        "summary": "\n".join(out),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "error_count": len(error_lines),
        "lines": len(lines),
        "top_errors": dict(exc_counts.most_common(5)),
    }
