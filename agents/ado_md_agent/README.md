# ADO MD Personal Assistant

A two-graph agent for a Managing Director who oversees multiple squads:
- **ETL graph** runs daily — pulls from ADO via MCP, computes deterministic
  metrics, derives RAID items, asks the LLM to summarise key achievements,
  and writes the day's snapshot to Postgres.
- **Drill-down graph** answers ad-hoc questions like "Why is Payments behind?"
  using the snapshot, plus a live MCP query for the squad in question when the
  user asks for "current" or "today" data.

## Configuration
List your portfolio squads in `config.yaml`:
```yaml
ado:
  squads:
    - { name: "Payments",   areapath: "Portfolio\\Payments" }
    - { name: "Onboarding", areapath: "Portfolio\\Onboarding" }
    - { name: "Risk",       areapath: "Portfolio\\Risk" }
```

## Required env vars
- `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PAT` — for MCP access
- `ANTHROPIC_API_KEY` — default LLM
- `DATABASE_URL` — Postgres for the snapshot tables
- `ADO_MCP_COMMAND` / `ADO_MCP_ARGS` — if your MCP server is configured non-default

## Run via the website
The website backend schedules ETL daily and exposes:
- `GET  /dashboards/md` — current dashboard JSON
- `POST /dashboards/md/drill` (SSE) — drill-down chat
- `POST /agents/ado_md/etl/trigger` — force a fresh ETL run

## Run as a standalone agent file
```bash
# Daily snapshot
python -m agents.ado_md_agent etl
python -m agents.ado_md_agent etl --date 2026-04-29

# Drill-down
python -m agents.ado_md_agent drill --q "Why is Payments behind?" --squad Payments
```

## Run with the LangGraph dev UI
```bash
cd Code/agents/ado_md_agent
langgraph dev    # exposes both `ado_md_etl` and `ado_md_drill` graphs
```
