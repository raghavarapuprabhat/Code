"""PII masking for probe results (architecture §9.7A).

API/DB results pass through here before being recorded as Evidence or streamed, so
emails, tokens, and card-like numbers never enter the LLM context or the trace.
"""
from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_TOKEN = re.compile(r"\b(?:eyJ[\w-]{10,}|sk-[\w-]{16,}|gh[pousr]_[\w]{20,}|[A-Za-z0-9_\-]{32,})\b")


def mask_text(text: str) -> str:
    if not text:
        return text
    text = _EMAIL.sub("«email»", text)
    text = _TOKEN.sub("«token»", text)
    text = _CARD.sub(lambda m: "«card»" if _luhnish(m.group(0)) else m.group(0), text)
    return text


def _luhnish(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    return 13 <= len(digits) <= 19


def mask_value(v: Any) -> Any:
    if isinstance(v, str):
        return mask_text(v)
    if isinstance(v, dict):
        return {k: mask_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [mask_value(x) for x in v]
    return v


def mask_rows(rows: list[dict]) -> list[dict]:
    return [mask_value(r) for r in rows]
