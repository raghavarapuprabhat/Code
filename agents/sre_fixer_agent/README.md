# SRE Fixer Agent

Takes a confirmed-bug handoff from the SRE Agent and produces a tested,
human-reviewable Pull Request on Azure Repos.

## Pipeline
```
context_load -> plan_fix -> apply_patch -> run_tests
   tests passed -> branch_commit -> open_pr
   tests failed -> analyze_failure
       should_retry & attempts<3 -> plan_fix (loop)
       otherwise                  -> raise_human
```

## Hard safety rails (cannot be overridden by the LLM or config)
- **Branch names** must start with the configured prefix (default `fix/sre-`).
- **Protected branches** (`main`, `master`, `release`, `develop`) are never touched.
- **No force-push** ever.
- **No branch delete** ever.
- **No writes inside `.git/`**; path-traversal blocked at the patch boundary.
- **Test commands are whitelisted** in `config.yaml` — the LLM picks a *key*,
  never an arbitrary command line. Shell metacharacters are rejected.
- **Tests must pass before PR**. Up to 3 plan/apply/test cycles, then human handoff.

## Prerequisites
- The repo to be fixed lives at the project_path stored by the Code Doc Agent
  (`code_projects.project_path`) and is a real git working tree with a remote.
- Azure DevOps PAT with **Code (Read & Write)** + **Pull Request (Read & Write)**
  on the target repository.

```bash
export AZURE_DEVOPS_ORG=https://dev.azure.com/myorg
export AZURE_DEVOPS_PAT=...
export ANTHROPIC_API_KEY=...
```

## Run via the SRE flow (recommended)
The SRE Agent emits a `handoff` event when it confirms a bug at high confidence;
the website backend forwards it to this agent and streams progress back to the UI.

## Run as a standalone agent file
```bash
python -m agents.sre_fixer_agent \
    --project <project_id> \
    --handoff ./handoff.json \
    --ado-project MyProject \
    --ado-repo MyRepo \
    --target-branch refs/heads/main
```

`handoff.json` shape:
```json
{
  "issue": {"title": "...", "description": "...", "stack_trace": "..."},
  "verdict": {
    "classification": "bug",
    "confidence": 0.9,
    "likely_files": ["src/.../X.java", "src/.../Y.java"],
    "rationale": "..."
  },
  "rag_hits": []
}
```

## Run with the LangGraph dev UI
```bash
cd Code/agents/sre_fixer_agent
langgraph dev
```
