"""Runtime probe tools — http_probe + db_query (architecture §9.7A, v0.4).

These are the planner-facing wrappers around the hard read-only rails in
``shared/probes``. They resolve a ProbeTarget from the environment registry (or an
ad-hoc target the user supplied via ask_user), enforce the **prod approval gate** and
the **probe budget**, and return a masked, citation-shaped observation. The executors
themselves enforce read-only (GET/HEAD only; SELECT/EXPLAIN only; host confinement;
secrets-as-env-names) regardless of what the planner asks for.

Discovery-first: the planner is expected to call discover_endpoints / discover_datasources
to learn target names before probing; when a target can't be resolved or prod approval
is missing, the tool returns guidance telling the planner to raise ``ask_user``.
"""
from __future__ import annotations

import structlog

from shared.probes import classify_env, db_probe, http_probe, resolve_target

logger = structlog.get_logger()


def _resolve(kind: str, name: str, env: str, ctx: dict) -> dict | None:
    # Ad-hoc targets supplied by the user mid-loop take precedence (ask_user target_resolution).
    for t in ctx.get("adhoc_targets", []) or []:
        if t.get("kind") == kind and t.get("name") == name and t.get("environment") == env:
            return t
    return resolve_target(kind, name, env, path=ctx.get("environments_path") or None)


def _budget_ok(ctx: dict) -> bool:
    b = ctx.get("budget") or {}
    return int(b.get("used_probes", 0)) < int(b.get("max_probes", 4))


def _spend(ctx: dict, target: dict) -> None:
    b = ctx.get("budget")
    if isinstance(b, dict):
        b["used_probes"] = int(b.get("used_probes", 0)) + 1


async def http_probe_tool(project_id: str, args: dict, ctx: dict) -> str:
    name = str(args.get("target", "")).strip()
    env = str(args.get("environment") or (ctx.get("facts") or {}).get("environment") or "dev")
    method = str(args.get("method", "GET"))
    path = str(args.get("path", "/"))
    if not name:
        return "(http_probe needs a 'target' name — call discover_endpoints first)"
    target = _resolve("http", name, env, ctx)
    if not target:
        return (f"(no HTTP target '{name}' for env '{env}' in the registry — raise action "
                f"ask_user blocks=target_resolution to get its base-URL env var)")
    if classify_env(env) == "prod" and not ctx.get("prod_approved"):
        return ("(PROD http probe requires approval — raise action ask_user "
                "blocks=probe_approval, then retry)")
    if not _budget_ok(ctx):
        return "(probe budget exhausted — conclude with the evidence in hand)"
    res = await http_probe(target, method, path, params=args.get("params"))
    _spend(ctx, target)
    logger.info("http_probe", target=name, env=env, ok=res.ok)
    return res.summary if res.ok else f"(http_probe error: {res.error})"


async def db_query_tool(project_id: str, args: dict, ctx: dict) -> str:
    name = str(args.get("target", "")).strip()
    env = str(args.get("environment") or (ctx.get("facts") or {}).get("environment") or "dev")
    sql = str(args.get("sql", ""))
    if not name:
        return "(db_query needs a 'target' name — call discover_datasources first)"
    if not sql.strip():
        return "(db_query needs a 'sql' SELECT/EXPLAIN statement)"
    target = _resolve("db", name, env, ctx)
    if not target:
        return (f"(no DB target '{name}' for env '{env}' in the registry — raise action "
                f"ask_user blocks=target_resolution to get its DSN env var)")
    if classify_env(env) == "prod" and not ctx.get("prod_approved"):
        return ("(PROD db probe requires approval — raise action ask_user "
                "blocks=probe_approval, then retry)")
    if not _budget_ok(ctx):
        return "(probe budget exhausted — conclude with the evidence in hand)"
    res = await db_probe(target, sql)
    _spend(ctx, target)
    logger.info("db_query", target=name, env=env, ok=res.ok)
    if not res.ok:
        return f"(db_query error: {res.error})"
    rows = res.detail.get("rows", [])
    preview = "; ".join(str(r) for r in rows[:3])
    return f"{res.summary}" + (f" — {preview}" if preview else "")
