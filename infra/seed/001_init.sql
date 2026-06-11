-- Initial schema for AI Agent Platform (POC)
-- Loaded automatically by Postgres on first container start.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ===================================================================
-- Conversations & Memory (summarize-only strategy)
-- ===================================================================
CREATE TABLE IF NOT EXISTS conversations (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name   TEXT NOT NULL,
    scope_key    TEXT,
    user_id      TEXT,
    title        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_conversations_agent_scope
    ON conversations (agent_name, scope_key);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    conversation_id    UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    running_summary    TEXT,
    message_count      INT NOT NULL DEFAULT 0,
    last_summarized_at TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recent_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_recent_messages_conv_created
    ON recent_messages (conversation_id, created_at DESC);

-- ===================================================================
-- Agent run telemetry (tokens, latency, cost)
-- ===================================================================
CREATE TABLE IF NOT EXISTS agent_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    agent_name      TEXT NOT NULL,
    node_name       TEXT,
    tokens_in       INT,
    tokens_out      INT,
    cost_usd        NUMERIC(10,6),
    duration_ms     INT,
    status          TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_created
    ON agent_runs (agent_name, created_at DESC);

-- ===================================================================
-- Code Documentation Agent
-- ===================================================================
CREATE TABLE IF NOT EXISTS code_projects (
    id            TEXT PRIMARY KEY,            -- sha256(absolute_path)
    project_path  TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    last_indexed  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS code_files (
    project_id        TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
    relative_path     TEXT NOT NULL,
    language          TEXT,
    loc               INT,
    last_hash         TEXT,
    last_analyzed_at  TIMESTAMPTZ,
    PRIMARY KEY (project_id, relative_path)
);

CREATE TABLE IF NOT EXISTS code_tree_graphs (
    project_id  TEXT PRIMARY KEY REFERENCES code_projects(id) ON DELETE CASCADE,
    graph_json  JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS code_file_summaries (
    project_id     TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
    relative_path  TEXT NOT NULL,
    summary_json   JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, relative_path)
);

-- Generated documentation (v0.2): markdown stored here is the source of truth
-- for rendering in the Documentation Hub. Confluence HTML is produced on demand
-- from content_md; chunks are embedded into Chroma collection docs_<project_id>.
CREATE TABLE IF NOT EXISTS generated_docs (
    project_id    TEXT NOT NULL REFERENCES code_projects(id) ON DELETE CASCADE,
    doc_id        TEXT NOT NULL,            -- e.g. '02_architecture'
    title         TEXT NOT NULL,
    audience      TEXT,                     -- management | architecture | developer
    sort_order    INT NOT NULL DEFAULT 0,
    content_md    TEXT NOT NULL,            -- full generated markdown
    content_hash  TEXT NOT NULL,            -- sha256(content_md) for incremental skip
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, doc_id)
);
CREATE INDEX IF NOT EXISTS ix_generated_docs_order
    ON generated_docs (project_id, sort_order);

-- ===================================================================
-- ADO Developer Assistant — preferences (last areapath etc.)
-- ===================================================================
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id         TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    last_areapath   TEXT,
    last_iteration  TEXT,
    preferences     JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, agent_name)
);

-- ===================================================================
-- ADO MD Assistant — daily snapshots
-- ===================================================================
CREATE TABLE IF NOT EXISTS squad_snapshot (
    snapshot_date         DATE NOT NULL,
    squad_name            TEXT NOT NULL,
    total_workitems       INT,
    in_progress           INT,
    done_this_sprint      INT,
    blocked               INT,
    overdue               INT,
    velocity_3sprint_avg  NUMERIC,
    utilization_pct       NUMERIC,
    PRIMARY KEY (snapshot_date, squad_name)
);

CREATE TABLE IF NOT EXISTS raid_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL,
    squad_name      TEXT NOT NULL,
    type            TEXT NOT NULL,         -- Risk | Assumption | Issue | Dependency
    title           TEXT,
    severity        TEXT,
    owner           TEXT,
    due_date        DATE,
    workitem_id     INT
);
CREATE INDEX IF NOT EXISTS ix_raid_snapshot_date_squad
    ON raid_snapshot (snapshot_date, squad_name);

CREATE TABLE IF NOT EXISTS key_achievement (
    id                     BIGSERIAL PRIMARY KEY,
    snapshot_date          DATE NOT NULL,
    squad_name             TEXT NOT NULL,
    achievement            TEXT NOT NULL,
    evidence_workitem_ids  INT[]
);
CREATE INDEX IF NOT EXISTS ix_key_achievement_date_squad
    ON key_achievement (snapshot_date, squad_name);
