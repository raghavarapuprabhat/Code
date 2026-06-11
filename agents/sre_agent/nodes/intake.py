"""Phase 1 — Understand: structure the raw report and normalize it into IssueFacts.

Keeps the shipped behavior (one LLM call to structure messy user text into an
IssueIntake) and adds a deterministic facts pass (§9.6): parse the stack trace into
ordered frames, derive the error signature + exception type, and infer the affected
component / environment — no extra LLM call.
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import IssueFacts, SREState
from ..tools.stacktrace import parse_stack_trace

logger = structlog.get_logger()

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "intake.md")


def _load_prompt() -> str:
    with open(PROMPT_PATH) as fh:
        return fh.read()


async def intake_node(state: SREState, *, config: dict) -> dict:
    issue = dict(state.get("issue") or {})

    # Structure messy free-text into an IssueIntake (skip when already structured, e.g. CSV).
    if not issue.get("description"):
        raw = state.get("user_message", "") or ""
        if not raw.strip():
            return {"issue": {}, "facts": IssueFacts().model_dump()}
        llm = build_adapter_from_config(config)
        prompt = _load_prompt().replace("{raw_text}", raw)
        resp = await llm.chat([{"role": "user", "content": prompt}])
        issue = _safe_json(resp.content) or {}
        issue.setdefault("title", raw.split("\n", 1)[0][:80])
        issue.setdefault("description", raw)

    facts = _derive_facts(issue)
    out: dict = {"issue": issue, "facts": facts}

    # Follow-up round: fold the reporter's answer into the ledger and refresh the
    # investigation budget so the loop actually re-investigates with the new fact.
    # (v0.4 replaces this whole-graph re-run with a LangGraph interrupt()/resume.)
    answer = (state.get("user_message") or "").strip()
    if state.get("classification_history") and answer and issue.get("description") != answer:
        issue["additional_context"] = (
            (issue.get("additional_context") or "") + f"\n[follow-up] {answer}"
        ).strip()
        evidence = [dict(e) for e in (state.get("evidence") or [])]
        evidence.append(
            {
                "id": f"E{len(evidence) + 1}",
                "source": "user",
                "citation": "reporter follow-up",
                "finding": answer[:300],
                "bears_on": [],
            }
        )
        out["evidence"] = evidence
        budget = dict(state.get("budget") or {})
        if budget:
            budget["used_steps"] = 0
            budget["used_tool_calls"] = 0
            out["budget"] = budget

    logger.info(
        "sre_understand_done",
        title=issue.get("title"),
        signature=facts.get("error_signature"),
        frames=len(facts.get("failing_frames", [])),
    )
    return out


def _derive_facts(issue: dict) -> dict:
    """Deterministic IssueFacts from the structured issue (no LLM).

    Parses both the stack_trace and the description (reporters often split the
    exception header into one and the frames into the other) and takes frames /
    exception type from whichever yields them, then recomputes the signature.
    """
    primary = parse_stack_trace(issue.get("stack_trace") or "")
    secondary = parse_stack_trace(issue.get("description") or "")

    frames = primary["frames"] or secondary["frames"]
    exception_type = primary["exception_type"] or secondary["exception_type"]

    # Recompute the signature from the merged exception type + top frame.
    top = frames[0] if frames else None
    sig_parts: list[str] = []
    if exception_type:
        sig_parts.append(exception_type)
    if top and (top.get("symbol") or top.get("relative_path")):
        loc = top.get("symbol") or top.get("relative_path")
        if top.get("line"):
            loc = f"{loc}:{top['line']}"
        sig_parts.append(f"@ {loc}")
    parsed = {
        "exception_type": exception_type,
        "error_signature": " ".join(sig_parts).strip(),
        "frames": frames,
    }

    component = issue.get("component")
    if not component and frames:
        top = frames[0]
        rel = top.get("relative_path") or ""
        component = os.path.splitext(os.path.basename(rel))[0] or None
    if not component and issue.get("title"):
        component = issue["title"].split()[0] if issue["title"].split() else None

    signature = parsed["error_signature"]
    if not signature:
        signature = (issue.get("title") or issue.get("description", "")[:80]).strip()

    symptoms = []
    desc = issue.get("description") or ""
    for line in desc.splitlines():
        line = line.strip()
        if line and not line.startswith(("at ", "File ")):
            symptoms.append(line[:160])
        if len(symptoms) >= 5:
            break

    facts = IssueFacts(
        error_signature=signature,
        exception_type=parsed["exception_type"],
        failing_frames=frames,
        component=component,
        environment=issue.get("environment"),
        symptoms=symptoms,
    )
    return facts.model_dump()


def _safe_json(text: str):
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
