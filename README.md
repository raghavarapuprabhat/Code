# AI Agent Platform — POC

Multi-agent platform for developers and management. See
`../Architecture/architecture.md` for the full design.

## Layout
```
Code/
├── infra/           Docker compose: Postgres + Chroma
├── shared/          Reusable libs (LLM adapter, memory, storage, MCP client)
├── backend/         FastAPI gateway (SSE chat + agent dispatch)
├── frontend/        React 18 + TypeScript + Vite SPA (white theme)
└── agents/          Standalone LangGraph agents
    └── code_doc_agent/   First agent — Java + React documentation
```

## Build status
| Component | Status |
|---|---|
| Postgres + Chroma docker-compose | done |
| Postgres schema seed (`infra/seed/001_init.sql`) | done |
| LLM adapter (LiteLLM, Anthropic default) | done |
| Memory manager (summarize-only) | done |
| ADO MCP client wrapper | done (skeleton) |
| FastAPI backend + SSE chat | done |
| React frontend (white theme): AppShell + Documentation Hub + project chatbot + pages | done — migrated from Lit (v0.2) |
| Code Documentation Agent (Phase 1) | done — full pipeline (ingest -> ast -> tree-graph -> incremental -> semantic -> cross-file -> verify -> doc-gen -> persist) |
| SRE Agent (Phase 2) | done — RAG-backed triage, multi-turn follow-up, CSV batch |
| SRE Fixer Agent (Phase 3) | done — plan -> apply -> test -> branch -> commit -> PR (Azure Repos), 3-attempt retry, hard safety rails |
| ADO MD Assistant (Phase 4) | done — daily ETL (APScheduler 06:00 UTC) + portfolio dashboard + drill-down chat |
| ADO Developer Assistant (Phase 5) | done — multi-turn chat, status report, consent-gated workitem updates, areapath memory |

## First-time setup

### 1. Prerequisites
- Python 3.11+
- Node 20+ (for the frontend)
- An Anthropic or DeepSeek API key (or any LiteLLM-supported provider — see `agents/code_doc_agent/config.yaml`)
- Docker — **optional**, only if you want Postgres instead of the default SQLite

### 2. Storage
The relational store defaults to **zero-config file-based SQLite** at
`Code/aiagent.db` — no Docker, no `DATABASE_URL`. The schema is auto-created on
backend startup and persists across restarts, so you index a project once.

Vector storage (Chroma) defaults to in-process persistent mode when `CHROMA_PATH`
is set (see `.env.example`); otherwise it expects a Chroma server on `:8001`.

**Optional — use Postgres + a Chroma server instead:**
```bash
cd Code/infra
docker compose up -d
# Postgres exposed on localhost:5433 (non-default port to avoid clashes)
# Chroma exposed on localhost:8001
# then set DATABASE_URL in .env to the postgresql+asyncpg URL
```

### 3. Backend
```bash
cd Code/backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp ../.env.example ../.env   # then edit ../.env to set ANTHROPIC_API_KEY or DEEPSEEK_API_KEY
export $(cat ../.env | xargs)
uvicorn app.main:app --reload --port 8000
```

### 4. Frontend
```bash
cd Code/frontend
npm install
npm run dev
# Open the printed URL (defaults to http://localhost:5174)
# Vite proxies /agents, /dashboards, /conversations to the backend on :8000
```
The React app (white theme) includes the **Documentation Hub** (`/docs`): browse
every generated document — stored in Postgres, rendered as Markdown or Confluence
HTML — and ask the **project chatbot** questions answered from the docs + code
summaries.

### 5. Run the Code Documentation Agent
Either through the UI (Code Doc page > "+ Index project") or directly:

```bash
# Standalone (no website needed):
cd Code
python -m agents.code_doc_agent /absolute/path/to/your/repo

# Incremental (after the first run):
python -m agents.code_doc_agent /absolute/path/to/your/repo --incremental
```

As of v0.2 the generated documents are stored in **Postgres** (`generated_docs`,
markdown = source of truth) and embedded in **Chroma** (`docs_<project_id>`) for the
project chatbot — they are **not** written to disk. View and download them (Markdown
or on-demand Confluence HTML) from the **Documentation Hub** (`/docs`) in the UI, or
fetch via `GET /agents/code_doc/projects/{id}/docs` and `.../docs/{doc_id}?format=`.

### 6. Run the SRE Agent (after Code Doc has indexed at least one project)
Through the UI: open the **SRE Triage** page, pick a project, paste an issue
or upload an incidents CSV.

Standalone:
```bash
# Single issue (text on stdin or via --text):
python -m agents.sre_agent --project <project_id> --text "Login returns 500 in prod"

# Batch CSV (writes a triaged CSV):
python -m agents.sre_agent --project <project_id> --csv ./incidents.csv
```
CSV input columns: `id, title, description, stack_trace, environment`.
Output adds: `verdict, confidence, rationale, likely_files, suggested_owner, next_step`.

### 7. Run the SRE Fixer Agent (after the SRE Agent confirms a bug)
Through the UI: in the SRE Triage page, when a verdict comes back as **bug**
the right pane shows a "Hand off to Fixer" panel — click it, supply ADO project
+ repo, and watch the audit-trail stream.

Standalone:
```bash
export AZURE_DEVOPS_ORG=https://dev.azure.com/myorg
export AZURE_DEVOPS_PAT=...

python -m agents.sre_fixer_agent \
    --project <project_id> \
    --handoff ./handoff.json \
    --ado-project MyProject \
    --ado-repo MyRepo \
    --target-branch refs/heads/main
```
The fixer will: plan a minimal patch -> apply -> run tests (whitelisted command) ->
branch off `main` with prefix `fix/sre-` -> commit -> push -> open a PR.
Up to 3 plan/test cycles; on failure the run terminates with `raised_human` and
no PR is opened. **Never auto-merges.**

### 8. Run the ADO MD Personal Assistant
First, configure squads to track in `agents/ado_md_agent/config.yaml`. Then:

Through the UI: open the **MD Dashboard** page. The first time you visit, click
"Run ETL now" to build a snapshot. After that, the daily APScheduler job runs at
06:00 UTC automatically.

Standalone:
```bash
# Build today's snapshot
python -m agents.ado_md_agent etl

# Snapshot a specific date (re-runs the day, overwriting RAID + achievements)
python -m agents.ado_md_agent etl --date 2026-04-29

# Drill-down without the website
python -m agents.ado_md_agent drill --q "Why is Payments behind?" --squad Payments
```

Backend endpoints:
- `GET  /dashboards/md` — heatmap + RAID + achievements + auto-derived attention
- `POST /dashboards/md/drill` (SSE) — single drill-down question with optional live MCP fallback when the question contains "today" / "currently" / "right now"
- `POST /dashboards/md/etl/trigger` — force a fresh ETL run

### 9. Run the ADO Developer Personal Assistant
Through the UI: open the **Dev Assistant** page. The first time you sign in,
you provide your ADO user (UPN/email) and display name; both are stored in
`localStorage`. The agent remembers your last areapath in Postgres
`user_preferences` and the next session pre-fills it.

Conversation: greet → confirm areapath → "status" or "update" → if update,
"what did you do today?" → review drafted updates → consent (yes/no/list ids)
→ apply via ADO MCP. **Workitems are never updated without explicit consent.**

Standalone REPL:
```bash
python -m agents.ado_dev_agent --user me@corp.com --name "Me Surname"
```

Backend endpoints:
- `POST /agents/ado_dev/chat`  (SSE) — multi-turn chat
- `POST /agents/ado_dev/reset` — clear the persisted conversation step

## Switching LLM provider
Use one of two approaches:

1) Global switch (recommended): set these in `.env` and all agents pick them up.
```bash
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY_ENV=DEEPSEEK_API_KEY
DEEPSEEK_API_KEY=sk-...
# Optional if required in your environment:
# LLM_BASE_URL=https://api.deepseek.com
```

2) Per-agent switch: edit each agent's `config.yaml` under `agents/*/config.yaml`.
For example, swap from Anthropic to DeepSeek:
```yaml
llm:
  provider: deepseek
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY
  # base_url: https://api.deepseek.com
```

You can also switch to Ollama:
```yaml
llm:
  provider: ollama
  model: llama3.1:70b
  base_url: http://localhost:11434
  api_key_env: ""
```
No code changes — the LiteLLM adapter routes accordingly.

## Distributing an agent as a "file"
Each agent under `agents/` is a self-contained Python package with its own
`pyproject.toml`, `config.yaml`, `langgraph.json`, prompts, and CLI entry point.
A developer can `pip install -e ./agents/code_doc_agent` and run it
independently of the website backend.
