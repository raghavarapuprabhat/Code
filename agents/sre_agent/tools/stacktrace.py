"""Deterministic stack-trace parsing (no LLM) — used during Understand (§9.6).

Handles the three stacks this platform documents (Java + JS/TS + Python) and degrades
to a best-effort signature for anything else. Output feeds IssueFacts: an error
signature, the exception type, and ordered frames resolved to ``file:line`` where the
trace exposes it.
"""
from __future__ import annotations

import re

from ..state import Frame

# Java:  at com.example.OrderService.price(OrderService.java:142)
_JAVA_FRAME = re.compile(
    r"^\s*at\s+(?P<symbol>[\w$.]+)\((?P<file>[\w$./-]+\.\w+):(?P<line>\d+)\)"
)
# Node/JS/TS:  at OrderService.price (/app/src/OrderService.ts:142:20)
#              at /app/src/OrderService.ts:142:20
_JS_FRAME = re.compile(
    r"^\s*at\s+(?:(?P<symbol>[\w$.<>\[\] ]+?)\s+\()?(?P<file>[^\s()]+?):(?P<line>\d+)(?::\d+)?\)?\s*$"
)
# Python:  File "/app/order_service.py", line 142, in price
_PY_FRAME = re.compile(
    r'^\s*File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<symbol>\S+)'
)

# Exception headers.
_JAVA_EXC = re.compile(r"(?P<type>(?:[\w$]+\.)*[\w$]*(?:Exception|Error|Throwable))(?::\s*(?P<msg>.*))?")
_PY_EXC = re.compile(r"^(?P<type>(?:\w+\.)*\w*(?:Error|Exception|Warning))(?::\s*(?P<msg>.*))?$")


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def parse_stack_trace(text: str) -> dict:
    """Parse a stack trace into ``{exception_type, message, error_signature, frames}``.

    ``frames`` is a list of :class:`Frame`-shaped dicts in trace order (top frame first).
    Everything is best-effort: an unrecognized blob still yields a usable signature from
    the first line.
    """
    if not text or not text.strip():
        return {"exception_type": None, "message": "", "error_signature": "", "frames": []}

    _MAX_LINES = 4_000     # cap pre-processing to prevent OOM on adversarial payloads
    _MAX_FRAMES = 50       # we only use the top few frames; cap for safety
    lines = text.splitlines()[:_MAX_LINES]
    frames: list[Frame] = []
    exception_type: str | None = None
    message = ""

    for ln in lines:
        if len(frames) >= _MAX_FRAMES:
            break
        m = _JAVA_FRAME.match(ln) or _PY_FRAME.match(ln)
        if not m:
            m = _JS_FRAME.match(ln)
            # The JS pattern is greedy; ignore matches that captured a Java/Python-ish
            # file already handled, and require a path-looking file token.
            if m and "." not in _basename(m.group("file")):
                m = None
        if m:
            sym = (m.groupdict().get("symbol") or "").strip() or None
            frames.append(
                Frame(
                    raw=ln.strip(),
                    symbol=sym,
                    relative_path=_basename(m.group("file")),
                    line=int(m.group("line")),
                )
            )
            continue

        # Not a frame — look for an exception header (first one wins as the type).
        if exception_type is None:
            em = _JAVA_EXC.search(ln) or _PY_EXC.match(ln.strip())
            if em:
                exception_type = em.group("type").rsplit(".", 1)[-1]
                message = (em.groupdict().get("msg") or "").strip()

    # Build the signature: "<ExceptionType> @ <topAppFrame.symbol>:<line>".
    top = frames[0] if frames else None
    sig_parts: list[str] = []
    if exception_type:
        sig_parts.append(exception_type)
    elif lines:
        sig_parts.append(lines[0].strip()[:80])
    if top and (top.symbol or top.relative_path):
        loc = top.symbol or top.relative_path
        if top.line:
            loc = f"{loc}:{top.line}"
        sig_parts.append(f"@ {loc}")
    error_signature = " ".join(sig_parts).strip()

    return {
        "exception_type": exception_type,
        "message": message,
        "error_signature": error_signature,
        "frames": [f.model_dump() for f in frames],
    }
