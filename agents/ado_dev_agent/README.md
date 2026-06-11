# ADO Developer Personal Assistant

A multi-turn chat agent for individual developers managing their ADO workitems.

## Conversation flow
```
greet
  -> await_areapath ("use 'X' or different?")
  -> await_intent  ("status report or update tasks?")
       status   -> compute & show status report (END)
       update   -> await_what_done
                 -> await_consent (review drafted updates)
                       all   -> apply via MCP
                       none  -> cancel
                       subset/edit -> handled
```

## Hard rails
- **Never updates a workitem without explicit user consent.** This is enforced
  at the node level — the consent prompt always runs before `update_workitem`.
- The user's last areapath / iteration are remembered in `user_preferences`.

## Required env vars
- `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PAT` — for MCP access
- `ANTHROPIC_API_KEY` — default LLM
- `DATABASE_URL` — Postgres for prefs

## Run via the website
Open the **Dev Assistant** page. Each user gets their own conversation
keyed by user_id; the next session starts pre-filled with the last areapath.

## Run as a standalone agent file
```bash
python -m agents.ado_dev_agent --user me@corp.com --name "Me Surname"
```
A small REPL prints assistant output and reads your replies from stdin.
The CLI persists prefs to the same Postgres `user_preferences` row the website
uses, so switching between CLI and UI is seamless.

## Run with the LangGraph dev UI
```bash
cd Code/agents/ado_dev_agent
langgraph dev
```
