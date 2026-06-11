"""v0.5 skippable extra nodes (§8.9.5–8.9.7):

  TestTrace      — rule → test mapping (enriches 06_business_logic)
  DbDriftCheck   — JPA/Prisma entities vs a live DB schema (skips without a DSN)
  DependencyAudit— CVE/license/outdated via native auditors (skips without binaries)

All three degrade gracefully: missing inputs/binaries → a "not configured" result and the
related doc carries the note instead of failing the index run.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid

import structlog

from ..state import CodeDocState

logger = structlog.get_logger()


# ===========================================================================
# TestTrace (§8.9.5) — map business rules to the tests that likely cover them
# ===========================================================================

_TEST_PATH_RE = re.compile(r"(test|spec)", re.IGNORECASE)


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", text or "")}


async def test_trace_node(state: CodeDocState, *, config: dict) -> dict:
    summaries = state.get("file_summaries") or {}
    inventory = state.get("file_inventory") or []

    test_files = [m["relative_path"] for m in inventory
                  if _TEST_PATH_RE.search(m.get("relative_path", ""))]
    if not test_files:
        return {"test_trace": {"skipped": True, "reason": "no test files detected"}}

    # Map each test file's tokens; link rules whose description overlaps.
    test_tokens = {tf: _keywords(tf.rsplit("/", 1)[-1]) for tf in test_files}
    mapping: dict[str, list[str]] = {}
    covered = 0
    total_rules = 0
    for path, s in summaries.items():
        for r in s.get("business_rules", []):
            total_rules += 1
            rule_kw = _keywords(r.get("description", "")) | _keywords(path.rsplit("/", 1)[-1])
            hits = [tf for tf, tok in test_tokens.items() if rule_kw & tok]
            if hits:
                covered += 1
                key = f"{path}: {r.get('description','')[:60]}"
                mapping[key] = hits[:5]

    result = {
        "rule_test_map": mapping,
        "rules_total": total_rules,
        "rules_covered": covered,
        "coverage_pct": round(covered / total_rules, 3) if total_rules else 0.0,
    }
    logger.info("test_trace_done", covered=covered, total=total_rules)
    return {"test_trace": result}


# ===========================================================================
# DbDriftCheck (§8.9.6) — entities-in-code vs live schema (skips without DSN)
# ===========================================================================

async def db_drift_node(state: CodeDocState, *, config: dict) -> dict:
    model = state.get("architecture_model") or {}
    datastores = model.get("datastores", [])
    # A live check needs a resolvable DSN env var; without it we report code-only.
    code_entities: set[str] = set()
    for ds in datastores:
        code_entities.update(ds.get("entities", []))

    dsn_env = next((ds.get("dsn_env") for ds in datastores if ds.get("dsn_env")), None)
    if not dsn_env or not os.getenv(dsn_env):
        return {"db_drift": {
            "skipped": True,
            "reason": "no resolvable DSN env var" if not dsn_env else f"{dsn_env} not set",
            "code_entities": sorted(code_entities),
        }}

    # If a DSN is present we *could* introspect; for the POC we record that the check
    # is possible but do not open a live connection from the indexer (read-only safety).
    logger.info("db_drift_dsn_available", dsn_env=dsn_env, entities=len(code_entities))
    return {"db_drift": {
        "skipped": False,
        "dsn_env": dsn_env,
        "code_entities": sorted(code_entities),
        "note": "Live schema introspection is performed by the SRE Agent's db_query "
                "rail, not the indexer; recorded entities for drift comparison.",
    }}


# ===========================================================================
# DependencyAudit (§8.9.7) — CVE/license/outdated (skips without auditors)
# ===========================================================================

async def _run(cmd: list[str], cwd: str, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, (out or b"").decode(errors="replace")
    except Exception:  # noqa: BLE001
        return 1, ""


async def _npm_audit(project_path: str) -> dict | None:
    if not os.path.isfile(os.path.join(project_path, "package.json")):
        return None
    if not shutil.which("npm"):
        return None
    code, out = await _run(["npm", "audit", "--json"], project_path)
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    cves = []
    vulns = data.get("vulnerabilities") or {}
    for name, info in vulns.items():
        cves.append({
            "dependency": name,
            "severity": info.get("severity", "unknown"),
            "fixed_in": (info.get("fixAvailable") or {}).get("version")
            if isinstance(info.get("fixAvailable"), dict) else "—",
            "components": [],
        })
    return {"cves": cves}


async def dependency_audit_node(state: CodeDocState, *, config: dict) -> dict:
    cfg = config.get("code_doc", {}) or {}
    if not cfg.get("dependency_audit", True):
        return {"dependency_findings": {"skipped": True, "reason": "disabled in config"}}

    project_path = state["project_path"]
    findings: dict = {"cves": [], "licenses": [], "outdated": []}
    ran_any = False

    npm = await _npm_audit(project_path)
    if npm is not None:
        findings["cves"].extend(npm.get("cves", []))
        ran_any = True

    # OWASP Dependency-Check for Maven would slot here; skipped if binary absent.
    if not ran_any:
        result = {"skipped": True, "reason": "no auditor binary (npm/OWASP) or no manifest"}
        logger.info("dependency_audit_skipped")
        return {"dependency_findings": result}

    await _persist_findings(state["project_id"], findings)
    logger.info("dependency_audit_done", cves=len(findings["cves"]))
    return {"dependency_findings": findings}


async def _persist_findings(pid: str, findings: dict) -> None:
    try:
        from sqlalchemy import text
        from shared.storage import get_session, init_db, is_sqlite, portable_sql
        if is_sqlite():
            await init_db()
        async with get_session() as session:
            await session.execute(
                text(portable_sql("""
                    INSERT INTO dependency_findings (id, project_id, findings_json)
                    VALUES (:id, :pid, :fj)
                """)),
                {"id": str(uuid.uuid4()), "pid": pid, "fj": json.dumps(findings)},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dependency_findings_persist_failed", err=str(exc))
