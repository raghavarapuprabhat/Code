"""Batch clustering pipeline (architecture §9.17.2).

500 tickets are usually ~12 problems. Before running any LLM loops the batch
pipeline normalises rows to IssueFacts (deterministic, no LLM), clusters them by
error signature + top-frame + component, investigates ONE representative per cluster
under the full batch budget, then propagates the verdict to every cluster member with
a per-row sanity check.

Output CSV gains: cluster_id, cluster_size, representative (bool).
Cost drops roughly by the clustering factor; the cluster table is the executive
summary ("your backlog is 12 root causes, here they are by frequency").
"""
from __future__ import annotations

import difflib
import math
from typing import Any

from .tools.stacktrace import parse_stack_trace


def _norm_sig(issue: dict) -> str:
    """Deterministic signature key for clustering — no LLM."""
    st = parse_stack_trace(issue.get("stack_trace") or issue.get("description") or "")
    exc = st.get("exception_type") or ""
    frames = st.get("frames") or []
    top_frame = frames[0].get("relative_path") or frames[0].get("symbol") or "" if frames else ""
    # Combine the exception type, top frame, and first word of the title.
    title_word = (issue.get("title") or "").split()[0] if issue.get("title") else ""
    return f"{exc}::{top_frame}::{title_word}".lower().strip("::")


def _sig_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def cluster_rows(rows: list[dict], *, threshold: float = 0.83) -> list[dict]:
    """Assign a ``cluster_id`` to each row.

    Pass 1 — exact signature buckets (fastest).
    Pass 2 — assign remaining rows to the nearest existing cluster centroid (embedding
    similarity is the architecture ideal; for the local POC we use edit-distance ratio
    on the signature string which is good enough for same-exception clustering).
    """
    sigs = [_norm_sig(r) for r in rows]
    centroids: list[tuple[int, str]] = []  # (cluster_id, centroid_sig)
    assignments: list[int] = []

    for i, sig in enumerate(sigs):
        matched = -1
        best_score = 0.0
        for cid, csig in centroids:
            # Exact match first.
            if sig == csig:
                matched = cid
                break
            score = _sig_similarity(sig, csig)
            if score >= threshold and score > best_score:
                best_score = score
                matched = cid
        if matched == -1:
            matched = len(centroids)
            centroids.append((matched, sig))
        assignments.append(matched)

    return assignments


def build_clusters(rows: list[dict], assignments: list[int]) -> list[dict[str, Any]]:
    """Group rows by cluster; elect the representative (most complete issue text)."""
    from collections import defaultdict
    groups: dict[int, list[int]] = defaultdict(list)
    for i, cid in enumerate(assignments):
        groups[cid].append(i)

    clusters = []
    for cid, indices in sorted(groups.items()):
        # Pick the rep: the row with the longest combined text (best described issue).
        rep_idx = max(indices, key=lambda i: len(
            (rows[i].get("description") or "") + (rows[i].get("stack_trace") or "")
        ))
        clusters.append({
            "cluster_id": cid,
            "indices": indices,
            "representative_idx": rep_idx,
            "size": len(indices),
        })
    return sorted(clusters, key=lambda c: c["size"], reverse=True)


async def sanity_check_row(row: dict, verdict: dict, llm) -> bool:
    """Quick LLM glance: does this row fit the cluster verdict? Misfits get
    their own mini-investigation."""
    from shared.llm_adapter.client import LLMResponse
    prompt = (
        "Does this issue row fit the root cause below? Reply YES or NO only.\n\n"
        f"Root cause: {verdict.get('root_cause', '')[:300]}\n\n"
        f"Issue title: {row.get('title', '')}\n"
        f"Issue description: {row.get('description', '')[:300]}"
    )
    try:
        resp = await llm.chat([{"role": "user", "content": prompt}])
        return "YES" in (resp.content or "").upper()
    except Exception:  # noqa: BLE001
        return True  # assume fit on error to avoid flooding solo investigations


async def triage_with_clustering(
    *,
    project_id: str,
    rows: list[dict],
    config: dict,
    build_graph_fn,          # from graph.py — avoids circular import
    load_config_fn,
) -> list[dict]:
    """Full batch pipeline: cluster → investigate reps → propagate → sanity-check."""
    from shared.llm_adapter import build_adapter_from_config

    sre_cfg = config.get("sre", {}) or {}
    cluster_cfg = sre_cfg.get("batch", {}) or {}
    should_cluster = cluster_cfg.get("cluster", True)
    threshold = float(cluster_cfg.get("cluster_similarity_threshold", 0.83))
    max_rows = int(sre_cfg.get("csv_max_rows", 500))
    rows = rows[:max_rows]

    if not should_cluster or len(rows) <= 3:
        # Fall through to the flat per-row triage.
        return []   # caller uses triage_csv instead

    assignments = cluster_rows(rows, threshold=threshold)
    clusters = build_clusters(rows, assignments)

    llm = build_adapter_from_config(config)
    g = build_graph_fn(config)
    out_rows: list[dict] = [{}] * len(rows)

    for cl in clusters:
        rep_idx = cl["representative_idx"]
        rep_row = rows[rep_idx]
        issue = {k: rep_row.get(k, "") for k in ("title", "description", "stack_trace", "environment", "repro_steps", "additional_context")}
        state = {"project_id": project_id, "issue": issue, "user_message": "", "batch": True}
        result = await g.ainvoke(state)
        verdict = result.get("verdict") or {}
        base = {
            "verdict": verdict.get("classification"),
            "confidence": verdict.get("confidence"),
            "root_cause": verdict.get("root_cause", ""),
            "rationale": verdict.get("rationale", ""),
            "related_files": ";".join(verdict.get("likely_files", [])),
            "regression_commit": _regression_from(result),
            "suggested_owner": verdict.get("suggested_owner") or "",
            "next_step": verdict.get("next_step", ""),
            "cluster_id": cl["cluster_id"],
            "cluster_size": cl["size"],
            "representative": False,
        }
        # Rep row gets the full verdict directly.
        rep_out = dict(base)
        rep_out["id"] = rep_row.get("id") or rep_row.get("ID") or ""
        rep_out["title"] = rep_row.get("title", "")
        rep_out["representative"] = True
        out_rows[rep_idx] = rep_out

        # Propagate to cluster members; sanity-check each one.
        for i in cl["indices"]:
            if i == rep_idx:
                continue
            row = rows[i]
            fits = await sanity_check_row(row, verdict, llm)
            if fits:
                m = dict(base)
                m["id"] = row.get("id") or row.get("ID") or ""
                m["title"] = row.get("title", "")
                out_rows[i] = m
            else:
                # Misfit — give it a solo mini-investigation.
                solo_issue = {k: row.get(k, "") for k in ("title", "description", "stack_trace", "environment")}
                solo_state = {"project_id": project_id, "issue": solo_issue, "user_message": "", "batch": True}
                solo_result = await g.ainvoke(solo_state)
                solo_v = solo_result.get("verdict") or {}
                out_rows[i] = {
                    "id": row.get("id") or row.get("ID") or "",
                    "title": row.get("title", ""),
                    "verdict": solo_v.get("classification"),
                    "confidence": solo_v.get("confidence"),
                    "root_cause": solo_v.get("root_cause", ""),
                    "rationale": solo_v.get("rationale", ""),
                    "related_files": ";".join(solo_v.get("likely_files", [])),
                    "regression_commit": _regression_from(solo_result),
                    "suggested_owner": solo_v.get("suggested_owner") or "",
                    "next_step": solo_v.get("next_step", ""),
                    "cluster_id": f"solo-{i}",
                    "cluster_size": 1,
                    "representative": True,
                }

    return out_rows


def _regression_from(state: dict) -> str:
    import re
    for e in state.get("evidence", []) or []:
        if e.get("source") == "git":
            m = re.search(r"\b([0-9a-f]{7,40})\b", e.get("citation", "") + " " + e.get("finding", ""))
            if m:
                return m.group(1)
    return ""
