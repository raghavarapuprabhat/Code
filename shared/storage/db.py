"""Async SQLAlchemy engine + session helpers.

Default backend is **file-based SQLite** (zero-config, no Docker/Postgres): the
DB lives at ``<repo>/Code/aiagent.db`` and persists across restarts, so a project
only needs indexing once. Set ``DATABASE_URL`` (e.g. ``postgresql+asyncpg://...``,
or a custom ``sqlite+aiosqlite:///...`` path) to override.

Because a couple of queries in the codebase use Postgres-only syntax inline
(``now()``, ``CAST(:x AS JSONB)``, ``NULLS LAST``), ``portable_sql()`` rewrites
those to their SQLite equivalents when the active dialect is SQLite. Call it on
any raw SQL string before wrapping it in ``text()`` — it's a no-op on Postgres.
"""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Default DB file lives at the repo's Code/ root (this file is Code/shared/storage/db.py),
# so the path is stable whether the backend runs from Code/backend or an agent from Code/.
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "aiagent.db"
DEFAULT_URL = f"sqlite+aiosqlite:///{_DEFAULT_DB_PATH}"

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None
_is_sqlite: bool = True


def _resolve_url() -> str:
    url = os.getenv("DATABASE_URL")
    return url if url else DEFAULT_URL


def get_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _is_sqlite
    if _engine is None:
        db_url = url or _resolve_url()
        _is_sqlite = db_url.startswith("sqlite")
        if _is_sqlite:
            is_memory = ":memory:" in db_url or "mode=memory" in db_url
            connect_args: dict = {"check_same_thread": False}
            kwargs: dict = {"future": True, "connect_args": connect_args}
            # A shared in-memory DB must use StaticPool (one connection) or each
            # session gets its own empty DB. A file DB can use the normal pool.
            if is_memory:
                kwargs["poolclass"] = StaticPool
            _engine = create_async_engine(db_url, **kwargs)

            @event.listens_for(_engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _rec):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        else:
            _engine = create_async_engine(db_url, pool_pre_ping=True, future=True)
    return _engine


def is_sqlite() -> bool:
    """Whether the active engine is SQLite (engine is created on first call)."""
    get_engine()
    return _is_sqlite


def iso_ts(value) -> str | None:  # noqa: ANN001
    """Normalize a DB timestamp to an ISO string across dialects.

    Postgres returns ``datetime`` objects (which have ``.isoformat()``); SQLite
    stores/returns timestamps as plain strings. Returns None for falsy values.
    """
    if not value:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def portable_sql(sql: str) -> str:
    """Rewrite Postgres-only constructs to SQLite when the dialect is SQLite.

    Handled: ``now()`` -> ``CURRENT_TIMESTAMP``; ``CAST(x AS JSONB)`` -> ``CAST(x AS TEXT)``;
    strip ``NULLS LAST``/``NULLS FIRST``. No-op on Postgres.
    """
    if not is_sqlite():
        return sql
    out = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
    out = re.sub(r"\bAS\s+JSONB\b", "AS TEXT", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+NULLS\s+(LAST|FIRST)\b", "", out, flags=re.IGNORECASE)
    return out


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
