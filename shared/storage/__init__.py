"""Storage helpers: SQLAlchemy async DB (SQLite default / Postgres) + Chroma."""
from .db import (
    get_engine,
    get_session,
    get_session_factory,
    is_sqlite,
    iso_ts,
    portable_sql,
)
from .schema import init_db

__all__ = [
    "get_engine",
    "get_session",
    "get_session_factory",
    "is_sqlite",
    "iso_ts",
    "portable_sql",
    "init_db",
    "ChromaStore",
]


def __getattr__(name: str):
    # Lazy import so importing storage doesn't require chromadb unless needed.
    if name == "ChromaStore":
        from .vector import ChromaStore

        return ChromaStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
