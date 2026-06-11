# Code Documentation Agent

Standalone LangGraph agent that produces deep, citation-backed documentation
for Java + React codebases. Output is written as Markdown + Confluence-compatible
HTML into `<project_path>/.docs/`.

## Run as part of the platform
The website backend imports this package directly. Start the platform with
`docker compose up` + `uvicorn app.main:app` and trigger via the UI or:

```bash
curl -X POST http://localhost:8000/agents/code_doc/index \
  -H 'content-type: application/json' \
  -d '{"project_path": "/abs/path/to/repo", "mode": "full"}'
```

## Run as a standalone agent file (no website)
```bash
cd <repo>/Code
pip install -e ./agents/code_doc_agent
export ANTHROPIC_API_KEY=sk-...
export DATABASE_URL=postgresql+asyncpg://aiagent:aiagent_local_password@localhost:5433/aiagent
python -m agents.code_doc_agent /abs/path/to/repo
# incremental re-run after code changes:
python -m agents.code_doc_agent /abs/path/to/repo --incremental
```

## Run with the LangGraph dev UI (visual debugger)
```bash
cd <repo>/Code/agents/code_doc_agent
langgraph dev
```

## Switching LLM provider
Edit `config.yaml`:
```yaml
llm:
  provider: ollama
  model: llama3.1:70b
  base_url: http://localhost:11434
  api_key_env: ""
```

No code changes required — the LiteLLM adapter handles the rest.
