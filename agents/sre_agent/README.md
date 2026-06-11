# SRE Agent

Triages reported issues against the documentation produced by the Code Doc Agent.
For each issue it produces a verdict — `bug`, `not_a_bug`, or `needs_more_info` —
with cited rationale, likely files, and a next step (which can be auto-handed
off to the SRE Fixer Agent).

## Prerequisites
1. The Code Doc Agent has been run for the target project (so a Chroma collection
   `code_<project_id>` exists).
2. Postgres + Chroma are up (`docker compose up -d` from `infra/`).
3. `ANTHROPIC_API_KEY` is set.

## Run via the website
Open the SRE page in the UI, pick a project, and start chatting — or upload a
CSV of incidents for batch triage.

## Run as a standalone agent file
Single issue:
```bash
python -m agents.sre_agent --project <project_id> --text "Login returns 500 in prod"
```

CSV batch (writes a triaged CSV next to the input):
```bash
python -m agents.sre_agent --project <project_id> --csv ./incidents.csv
```

CSV column expectations: `id, title, description, stack_trace, environment`
(extras are ignored). Output adds: `verdict, confidence, rationale, likely_files,
suggested_owner, next_step`.

## Run with the LangGraph dev UI
```bash
cd Code/agents/sre_agent
langgraph dev
```
