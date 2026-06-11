"""Phase 8c — DriftDigest (§8.9.4): diff the prior vs current Architecture Model.

Emits a dated digest entry: new/removed components & connectors, new endpoints, new
external systems, layer-violation deltas, hotspot movement, and requirement impact
(components traced to work items that changed). Stored append-only in `arch_digests`;
the `16_change_digest` doc renders the latest entry.

First index (no prior model) → empty digest; the doc renders a "first index" note.
Pure-deterministic diff; no LLM.
"""
from __future__ import annotations

import json
import uuid
from datetime import date

import structlog
from sqlalchemy import text

from shared.storage import get_session, init_db, is_sqlite, portable_sql
from ..state import CodeDocState

logger = structlog.get_logger()


def _names(items: list[dict], key: str) -> set[str]:
    return {str(i.get(key, "")) for i in items if i.get(key)}


def _diff_models(prev: dict, curr: dict) -> list[str]:
    lines: list[str] = []

    prev_comps = _names(prev.get("components", []), "name")
    curr_comps = _names(curr.get("components", []), "name")
    added_c = sorted(curr_comps - prev_comps)
    removed_c = sorted(prev_comps - curr_comps)
    if added_c:
        lines.append(f"- **New components:** {', '.join(added_c)}")
    if removed_c:
        lines.append(f"- **Removed components:** {', '.join(removed_c)}")

    def _conn_key(c: dict) -> str:
        return f"{c.get('from','')}->{c.get('to','')}"
    prev_conn = {_conn_key(c) for c in prev.get("connectors", [])}
    curr_conn = {_conn_key(c) for c in curr.get("connectors", [])}
    added_conn = sorted(curr_conn - prev_conn)
    if added_conn:
        lines.append(f"- **New connectors:** {', '.join(added_conn[:10])}")

    def _ep_key(e: dict) -> str:
        return f"{e.get('method','')} {e.get('path','')}"
    prev_ep = {_ep_key(e) for e in prev.get("endpoints", [])}
    curr_ep = {_ep_key(e) for e in curr.get("endpoints", [])}
    added_ep = sorted(curr_ep - prev_ep)
    if added_ep:
        lines.append(f"- **New endpoints:** {', '.join(added_ep[:10])}")

    prev_ext = _names(prev.get("external_systems", []), "name")
    curr_ext = _names(curr.get("external_systems", []), "name")
    added_ext = sorted(curr_ext - prev_ext)
    if added_ext:
        lines.append(f"- **New external integrations:** {', '.join(added_ext)}")

    prev_viol = len([v for l in prev.get("layers", []) for v in l.get("violations", [])])
    curr_viol = len([v for l in curr.get("layers", []) for v in l.get("violations", [])])
    if curr_viol != prev_viol:
        delta = curr_viol - prev_viol
        lines.append(f"- **Layer violations:** {prev_viol} → {curr_viol} "
                     f"({'+' if delta > 0 else ''}{delta})")

    # Hotspot top-file movement.
    prev_hot = [h.get("file") for h in prev.get("quality", {}).get("hotspots", [])][:10]
    curr_hot = [h.get("file") for h in curr.get("quality", {}).get("hotspots", [])][:10]
    entered = [f for f in curr_hot if f not in prev_hot]
    if entered:
        lines.append(f"- **New hotspots:** {', '.join(str(f) for f in entered[:5])}")

    return lines


async def _store_snapshot(pid: str, model: dict, model_hash: str) -> None:
    """Persist the current model JSON so the NEXT run can diff against it."""
    try:
        async with get_session() as session:
            await session.execute(
                text(portable_sql("""
                    INSERT INTO arch_model_snapshots (project_id, model_json, model_hash, created_at)
                    VALUES (:pid, :mj, :mh, CURRENT_TIMESTAMP)
                    ON CONFLICT (project_id) DO UPDATE SET
                        model_json = excluded.model_json, model_hash = excluded.model_hash,
                        created_at = CURRENT_TIMESTAMP
                """)),
                {"pid": pid, "mj": json.dumps(model, default=str), "mh": model_hash},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot_store_failed", err=str(exc))


async def _load_snapshot(pid: str) -> dict | None:
    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    text("SELECT model_json FROM arch_model_snapshots WHERE project_id = :p"),
                    {"p": pid},
                )
            ).first()
        if row and row.model_json:
            return json.loads(row.model_json)
    except Exception:  # noqa: BLE001 — table may not exist on first run
        return None
    return None


async def drift_digest_node(state: CodeDocState, *, config: dict) -> dict:
    pid = state["project_id"]
    curr_model = state.get("architecture_model") or {}
    curr_hash = state.get("model_hash", "")
    prev_hash = state.get("prev_model_hash")

    if is_sqlite():
        await init_db()

    # Load the previous full snapshot (written by the last run) and diff.
    prior = await _load_snapshot(pid)
    digest_md = ""
    if prior and curr_model:
        diff_lines = _diff_models(prior, curr_model)
        if diff_lines:
            req = state.get("traceability", {}) or {}
            impacted = _requirement_impact(diff_lines, req)
            today = date.today().isoformat()
            parts = [f"## {today}", ""] + diff_lines
            if impacted:
                parts.append(f"- **Requirements impact:** {impacted}")
            digest_md = "\n".join(parts)
            await _persist_digest(pid, digest_md, prev_hash, curr_hash)

    # Snapshot current model for the next run's diff.
    if curr_model:
        await _store_snapshot(pid, curr_model, curr_hash)

    logger.info("drift_digest_done", changed=bool(digest_md), had_prior=bool(prior))
    return {"drift_digest": digest_md}


def _requirement_impact(diff_lines: list[str], traceability: dict) -> str:
    """Name work items traced to components that changed in this diff."""
    changed_text = " ".join(diff_lines)
    impacted = []
    for r in traceability.get("matrix", []):
        for comp in r.get("components", []):
            if comp and comp in changed_text:
                impacted.append(f"#{r.get('work_item_id','')}")
                break
    uniq = sorted(set(impacted))
    return ", ".join(uniq[:10]) if uniq else ""


async def _persist_digest(pid: str, digest_md: str, h_from: str | None, h_to: str) -> None:
    try:
        async with get_session() as session:
            await session.execute(
                text(portable_sql("""
                    INSERT INTO arch_digests (id, project_id, period, digest_md, model_hash_from, model_hash_to)
                    VALUES (:id, :pid, :period, :md, :hf, :ht)
                """)),
                {"id": str(uuid.uuid4()), "pid": pid, "period": date.today().isoformat(),
                 "md": digest_md, "hf": h_from, "ht": h_to},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("digest_persist_failed", err=str(exc))
