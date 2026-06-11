"""Deterministic severity + blast-radius estimation (§9.17.6).

Called at Conclude (after the verdict is set) for confirmed bugs.  Reads the
Architecture Model's endpoint + flow catalogue to find what is reachable from the
failing component, computes a hotspot score from evidence density, and derives a
severity level WITHOUT an additional LLM call (deterministic rules first; LLM
narrates the summary only if the arch model has meaningful data).

Output stored in SREState.severity:
  {
    "level":               "critical" | "high" | "medium" | "low",
    "blast_radius":        "one-sentence prose",
    "endpoints_affected":  [...],
    "critical_flows":      [...],
    "hotspot_score":       0.0-1.0,
    "log_frequency_hint":  optional str,
    "rationale":           "prose",
  }
"""
from __future__ import annotations

import asyncio
import json


_LEVEL_THRESHOLDS = [
    (0.75, "critical"),
    (0.50, "high"),
    (0.25, "medium"),
]


def _hotspot_score(evidence: list[dict], facts: dict) -> float:
    """Proxy for how central the failing code is.

    Signals: number of evidence items bearing on the same file, how many unique
    hypothesis IDs are supported, and whether a regression commit is present.
    """
    if not evidence:
        return 0.0
    files = [e.get("citation", "") for e in evidence if e.get("source") == "code"]
    unique_files = len(set(files))
    total_ev = len(evidence)
    has_regression = any(e.get("source") == "git" for e in evidence)
    score = min(1.0, (total_ev / 20) * 0.5 + (unique_files / 5) * 0.3 + (0.2 if has_regression else 0.0))
    return round(score, 4)


def _level(hotspot: float, endpoints: list, critical_flows: list) -> str:
    """Map signals to a severity level."""
    adjusted = hotspot
    if any("auth" in str(ep).lower() or "payment" in str(ep).lower() for ep in endpoints):
        adjusted = min(1.0, adjusted + 0.25)
    if critical_flows:
        adjusted = min(1.0, adjusted + 0.15)
    for threshold, label in _LEVEL_THRESHOLDS:
        if adjusted >= threshold:
            return label
    return "low"


async def estimate_impact(
    *,
    project_id: str,
    verdict: dict,
    evidence: list[dict],
    facts: dict,
    config: dict,
) -> dict:
    """Compute severity + blast-radius for the given bug verdict.

    Falls back gracefully: if the Architecture Model is unavailable, returns a
    best-effort estimate from the evidence alone.
    """
    from ..tools.architecture import get_architecture, discover_endpoints

    hotspot = _hotspot_score(evidence, facts)
    endpoints: list = []
    critical_flows: list = []
    arch_summary = ""

    # Try to get architecture data for the affected component.
    try:
        likely = verdict.get("likely_files") or []
        component_hint: str | None = None
        if likely:
            # Heuristic: take the filename without extension as component hint.
            import os
            component_hint = os.path.splitext(os.path.basename(likely[0]))[0]

        arch_raw = await get_architecture(project_id, component_hint)
        if arch_raw and "(not found)" not in arch_raw and "(no architecture" not in arch_raw:
            arch_summary = arch_raw[:1500]
            # Parse endpoint list from architecture text.
            import re
            ep_matches = re.findall(r"(?:GET|POST|PUT|DELETE|PATCH)\s+(/[^\s,)\"]+)", arch_raw)
            endpoints = ep_matches[:10]

        ep_raw = await discover_endpoints(project_id, component_hint)
        if ep_raw and "(not found)" not in ep_raw:
            import re
            ep2 = re.findall(r"(?:GET|POST|PUT|DELETE|PATCH)\s+(/[^\s,)\"]+)", ep_raw)
            for ep in ep2:
                if ep not in endpoints:
                    endpoints.append(ep)
            endpoints = endpoints[:10]
    except Exception:  # noqa: BLE001
        pass

    level = _level(hotspot, endpoints, critical_flows)

    # Produce blast_radius prose from available signals (no LLM call needed for the label).
    if endpoints:
        blast_prose = f"{len(endpoints)} endpoint(s) reachable through the affected component: {', '.join(endpoints[:5])}"
    elif arch_summary:
        blast_prose = "Architecture model available but no endpoints mapped to this component."
    else:
        blast_prose = "Architecture model unavailable; blast radius estimated from evidence density only."

    return {
        "level": level,
        "blast_radius": blast_prose,
        "endpoints_affected": endpoints,
        "critical_flows": critical_flows,
        "hotspot_score": hotspot,
        "log_frequency_hint": None,
        "rationale": (
            f"Hotspot score {hotspot:.2f} from {len(evidence)} evidence items; "
            f"{len(endpoints)} endpoint(s) affected; level={level}."
        ),
    }
