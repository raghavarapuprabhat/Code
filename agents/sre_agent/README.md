# SRE Agent

An **agentic, hypothesis-driven investigator** (architecture §9). Rather than a
single-shot classifier, it runs a ReAct loop — **Understand → Ground → Hypothesize →
Investigate → Conclude** — that parses the stack trace, grounds the issue in the
generated docs + code summaries, forms competing root-cause hypotheses, then reads the
actual code / blames the suspect lines / checks the flow and recent commits to confirm
or refute them under a budget. It produces an **evidence-cited verdict** — `bug`,
`not_a_bug`, `needs_more_info`, or `external` — with a root-cause narrative,
`file:line` / `doc` / `commit` citations, a confidence score, and a full investigation
trace. A confirmed bug emits a structured handoff packet so the SRE Fixer starts at
PlanFix, not re-investigation.

### Investigation tools (read-only, §9.7)
`search_code_docs` (queries both `docs_<pid>` + `code_<pid>`), `get_doc`,
`fetch_code_snippet`, `get_business_rules`, `get_call_graph`, `get_flow`, `grep_code`,
`git_blame`, `git_log_recent`, `find_similar_issues`. Each is path/read-only guarded;
toggles and the per-investigation budget live in `config.yaml` under `sre.budget` /
`sre.tools`.

> Live runtime probes (`http_probe`/`db_query`), architecture-model tools, mid-loop
> `ask_user` (interrupt), and the v0.6 observability / clustering / repro-test /
> verify-after-fix / calibration / ADO-write-back enhancements are subsequent phases.

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

Each row runs the same loop under a tighter budget (no git/callgraph/grep — §9.14).
CSV column expectations: `id, title, description, stack_trace, environment`
(extras are ignored). Output adds: `verdict, confidence, root_cause, rationale,
related_files, regression_commit, suggested_owner, next_step`.

## Tests
```bash
python agents/sre_agent/tests/test_smoke.py      # no external services needed
```

## Run with the LangGraph dev UI
```bash
cd Code/agents/sre_agent
langgraph dev
```
