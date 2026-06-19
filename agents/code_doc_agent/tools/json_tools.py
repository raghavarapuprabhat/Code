"""Robust JSON extraction from LLM responses.

LLMs — especially Claude/Sonnet behind an OpenAI-compatible gateway that does not
enforce ``response_format`` — frequently wrap their JSON in prose ("Here is the
summary:"), markdown fences (```json ... ```), or trailing commentary. The naive
"first ``{`` to last ``}``" slice that the nodes used previously breaks whenever the
surrounding prose itself contains a brace, or when the model emits more than one
JSON-ish block.

``extract_json`` handles all of that deterministically:
  1. Strip markdown code fences (```json ... ``` / ``` ... ```), preferring fenced
     content when present (the model usually fences the real answer).
  2. Scan for the first balanced ``{...}`` (or ``[...]``) object, respecting strings
     and escapes so braces inside string literals don't throw off the depth count.
  3. Fall back to a plain ``json.loads`` of the whole (de-fenced) text.

Returns the parsed object/list, or ``None`` if nothing parses — callers treat
``None`` as "LLM produced no usable JSON" and fall back to deterministic data.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str | None) -> Any | None:
    if not text:
        return None
    raw = text.strip()

    # 1. Prefer fenced blocks — the model usually fences the real payload. Try each
    #    fenced chunk (and the whole de-fenced text) as candidate JSON.
    candidates: list[str] = []
    for m in _FENCE_RE.finditer(raw):
        candidates.append(m.group(1).strip())
    # De-fenced whole text (handles a single unterminated fence too).
    candidates.append(_strip_fences(raw))
    candidates.append(raw)

    for cand in candidates:
        obj = _try_parse(cand)
        if obj is not None:
            return obj
        # 2. Pull balanced object/array spans out of the candidate and try each —
        #    skips non-JSON braces in prose (e.g. "(see {note})") to reach the real one.
        for balanced in _balanced_spans(cand):
            obj = _try_parse(balanced)
            if obj is not None:
                return obj
    return None


def _try_parse(s: str) -> Any | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # Drop the opening fence line (``` or ```json) and any trailing fence.
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _balanced_spans(text: str):
    """Yield each balanced {...}/[...] span in order, ignoring braces inside strings.

    Yields successive top-level spans so a non-JSON brace group earlier in prose
    (e.g. "(see {note})") doesn't hide the real JSON that follows it."""
    pos = 0
    n = len(text)
    while pos < n:
        start = _next_open(text, pos)
        if start is None:
            return
        open_ch = text[start]
        close_ch = "}" if open_ch == "{" else "]"
        depth = 0
        in_str = False
        escape = False
        i = start
        closed = False
        while i < n:
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    yield text[start : i + 1]
                    pos = i + 1
                    closed = True
                    break
            i += 1
        if not closed:
            # Unbalanced from here on (truncated) — nothing more to find.
            return


def _next_open(text: str, frm: int) -> int | None:
    brace = text.find("{", frm)
    bracket = text.find("[", frm)
    candidates = [i for i in (brace, bracket) if i >= 0]
    return min(candidates) if candidates else None
