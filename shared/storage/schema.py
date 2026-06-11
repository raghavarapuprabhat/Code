"""SQLite-compatible schema initialization.

The Postgres schema lives in ``infra/seed/001_init.sql`` and is applied by the
docker-compose Postgres container. When running on the zero-config in-memory
SQLite default there is no container to seed, so we create an equivalent schema
here at startup (FastAPI lifespan) and defensively before agent indexing.

This mirrors the Postgres tables with SQLite-friendly types:
  JSONB        -> TEXT (JSON stored as a string)
  TIMESTAMPTZ  -> TEXT (ISO timestamps; DEFAULT CURRENT_TIMESTAMP)
  BIGSERIAL    -> INTEGER PRIMARY KEY AUTOINCREMENT
  INT[]        -> TEXT (JSON-encoded list)
  uuid_generate_v4() default -> handled in app code (we always pass ids)
"""
from __future__ import annotations

from sqlalchemy import text

from .db import get_session, is_sqlite

# Executed only for SQLite. Order matters for FK references.
_SQLITE_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id           TEXT PRIMARY KEY,
        agent_name   TEXT NOT NULL,
        scope_key    TEXT,
        user_id      TEXT,
        title        TEXT,
        created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_conversations_agent_scope ON conversations (agent_name, scope_key)",
    """
    CREATE TABLE IF NOT EXISTS conversation_summaries (
        conversation_id    TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
        running_summary    TEXT,
        message_count      INTEGER NOT NULL DEFAULT 0,
        last_summarized_at TEXT,
        updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recent_messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL,
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_recent_messages_conv_created ON recent_messages (conversation_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
        agent_name      TEXT NOT NULL,
        node_name       TEXT,
        tokens_in       INTEGER,
        tokens_out      INTEGER,
        cost_usd        REAL,
        duration_ms     INTEGER,
        status          TEXT,
        error           TEXT,
        created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_created ON agent_runs (agent_name, created_at)",
    """
    CREATE TABLE IF NOT EXISTS code_projects (
        id            TEXT PRIMARY KEY,
        project_path  TEXT NOT NULL UNIQUE,
        display_name  TEXT,
        last_indexed  TEXT,
        created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS code_files (
        project_id        TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
        relative_path     TEXT NOT NULL,
        language          TEXT,
        loc               INTEGER,
        last_hash         TEXT,
        last_analyzed_at  TEXT,
        PRIMARY KEY (project_id, relative_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS code_tree_graphs (
        project_id  TEXT PRIMARY KEY REFERENCES code_projects(id) ON DELETE CASCADE,
        graph_json  TEXT NOT NULL,
        updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS code_file_summaries (
        project_id     TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
        relative_path  TEXT NOT NULL,
        summary_json   TEXT NOT NULL,
        updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project_id, relative_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS generated_docs (
        project_id    TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
        doc_id        TEXT NOT NULL,
        title         TEXT NOT NULL,
        audience      TEXT,
        sort_order    INTEGER NOT NULL DEFAULT 0,
        content_md    TEXT NOT NULL,
        content_hash  TEXT NOT NULL,
        generated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project_id, doc_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_generated_docs_order ON generated_docs (project_id, sort_order)",
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
        user_id         TEXT NOT NULL,
        agent_name      TEXT NOT NULL,
        last_areapath   TEXT,
        last_iteration  TEXT,
        preferences     TEXT NOT NULL DEFAULT '{}',
        updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, agent_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS squad_snapshot (
        snapshot_date         TEXT NOT NULL,
        squad_name            TEXT NOT NULL,
        total_workitems       INTEGER,
        in_progress           INTEGER,
        done_this_sprint      INTEGER,
        blocked               INTEGER,
        overdue               INTEGER,
        velocity_3sprint_avg  REAL,
        utilization_pct       REAL,
        PRIMARY KEY (snapshot_date, squad_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raid_snapshot (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date   TEXT NOT NULL,
        squad_name      TEXT NOT NULL,
        type            TEXT NOT NULL,
        title           TEXT,
        severity        TEXT,
        owner           TEXT,
        due_date        TEXT,
        workitem_id     INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_raid_snapshot_date_squad ON raid_snapshot (snapshot_date, squad_name)",
    """
    CREATE TABLE IF NOT EXISTS key_achievement (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date          TEXT NOT NULL,
        squad_name             TEXT NOT NULL,
        achievement            TEXT NOT NULL,
        evidence_workitem_ids  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_key_achievement_date_squad ON key_achievement (snapshot_date, squad_name)",
]

_initialized = False


async def init_db(force: bool = False) -> None:
    """Create the SQLite schema if running on SQLite. No-op on Postgres.

    Idempotent (uses IF NOT EXISTS) and guarded so repeated calls are cheap.
    """
    global _initialized
    if not is_sqlite():
        return
    if _initialized and not force:
        return
    async with get_session() as session:
        for stmt in _SQLITE_DDL:
            await session.execute(text(stmt))
        await session.commit()
    _initialized = True
