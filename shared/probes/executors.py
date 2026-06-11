"""Read-only probe executors — HTTP + DB (architecture §9.7A, hard rails).

HTTP: methods allowlisted to GET/HEAD (config may add OPTIONS); the host is taken from
the resolved target's base URL only — a URL embedded in issue text can never be fetched.
DB: SQL is validated to a single SELECT/EXPLAIN (sql_guard), the connection is opened
read-only with a statement timeout, results are row-capped and PII-masked. Secrets are
read from env-var names at call time and never returned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import structlog

from .masking import mask_rows, mask_text
from .registry import get_secret
from .sql_guard import validate_select_sql

logger = structlog.get_logger()

_HTTP_METHODS = {"GET", "HEAD"}
_TIMEOUT_S = 5.0
_MAX_BYTES = 64_000


@dataclass
class ProbeResult:
    ok: bool
    summary: str                       # masked, human-readable — safe for Evidence/SSE
    detail: dict[str, Any] = field(default_factory=dict)
    error: str = ""


async def http_probe(
    target: dict, method: str, path: str, *, params: dict | None = None,
    allow_options: bool = False,
) -> ProbeResult:
    method = (method or "GET").upper()
    allowed = _HTTP_METHODS | ({"OPTIONS"} if allow_options else set())
    if method not in allowed:
        return ProbeResult(False, "", error=f"method {method} not allowed (read-only: {sorted(allowed)})")

    base_env = target.get("base_url_or_dsn_ref", "")
    base_url = get_secret(base_env)
    if not base_url:
        return ProbeResult(False, "", error=f"base URL env var {base_env} is unset")

    # Host confinement: path must be a path, not an absolute URL to elsewhere.
    if "://" in (path or ""):
        return ProbeResult(False, "", error="absolute URLs are not allowed in path (host confinement)")
    url = urljoin(base_url.rstrip("/") + "/", (path or "").lstrip("/"))
    base_host = urlparse(base_url).netloc
    if urlparse(url).netloc != base_host:
        return ProbeResult(False, "", error="resolved host does not match the target (host confinement)")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.request(method, url, params=params or None)
            body = resp.text[:_MAX_BYTES]
    except Exception as e:  # noqa: BLE001
        return ProbeResult(False, "", error=f"request failed: {e}")

    masked_body = mask_text(body)
    env = target.get("environment", "?")
    summary = f"{method} {path} → {resp.status_code} ({env})"
    if resp.status_code >= 400:
        summary += f"; body: {masked_body[:300]}"
    return ProbeResult(
        True, summary,
        detail={"status": resp.status_code, "body": masked_body, "env": env},
    )


async def db_probe(target: dict, sql: str) -> ProbeResult:
    dsn_env = target.get("base_url_or_dsn_ref", "")
    dsn = get_secret(dsn_env)
    if not dsn:
        return ProbeResult(False, "", error=f"DSN env var {dsn_env} is unset")

    dialect = "postgres" if dsn.startswith(("postgres", "postgresql")) else (
        "sqlite" if dsn.startswith("sqlite") else None
    )
    check = validate_select_sql(sql, dialect=dialect)
    if not check.ok:
        return ProbeResult(False, "", error=f"SQL rejected: {check.reason}")

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except Exception as e:  # noqa: BLE001
        return ProbeResult(False, "", error=f"sqlalchemy unavailable: {e}")

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        async with engine.connect() as conn:
            if dialect == "postgres":
                # Hard read-only + statement timeout at the session level.
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(text("SET statement_timeout = 5000"))
            result = await conn.execute(text(check.sql))
            rows = [dict(r._mapping) for r in result.fetchmany(50)]
    except Exception as e:  # noqa: BLE001
        return ProbeResult(False, "", error=f"query failed: {e}")
    finally:
        await engine.dispose()

    masked = mask_rows(rows)
    env = target.get("environment", "?")
    ro = "prod-ro" if target.get("environment", "").startswith("prod") else env
    summary = f"db:{target.get('name')} → {len(masked)} row(s) ({ro})"
    return ProbeResult(True, summary, detail={"rows": masked, "rowcount": len(masked), "env": env})
