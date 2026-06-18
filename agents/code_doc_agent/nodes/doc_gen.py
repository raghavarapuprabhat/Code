"""Phase 7 — render the document set as markdown, in memory (v0.2).

As of v0.2 the generated documents are NOT written to disk. They are returned in
state as ``generated_docs`` (doc_id -> markdown) and stored in Postgres + Chroma
by the persist node. Confluence HTML is produced on demand by the API from the
stored markdown (see shared.docs.markdown_to_confluence_html).
"""
from __future__ import annotations

import json
import os

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState
from ..tools.arch_docs import (
    render_adrs,
    render_architecture,
    render_deployment,
    render_external,
    render_quality,
)
from ..tools.mermaid_tools import (
    render_call_graph,
    render_er_diagram,
    render_module_diagram,
    render_sequence_diagram,
)

logger = structlog.get_logger()

_MGMT_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "management_overview.md")


def _load(path: str) -> str:
    with open(path) as fh:
        return fh.read()


async def doc_gen_node(state: CodeDocState, *, config: dict) -> dict:
    project_path = state["project_path"]  # used for the management-overview title

    llm = build_adapter_from_config(config)
    docs: dict[str, str] = {}

    # 1. Management overview (LLM-generated)
    mgmt_prompt = (
        _load(_MGMT_PROMPT_PATH)
        .replace("{project_name}", state.get("display_name") or os.path.basename(project_path))
        .replace("{modules_json}", json.dumps(state.get("modules", []), indent=2)[:30_000])
        .replace("{flows_json}", json.dumps(state.get("flows", []), indent=2)[:30_000])
    )
    mgmt_resp = await llm.chat([{"role": "user", "content": mgmt_prompt}])
    docs["01_management_overview.md"] = mgmt_resp.content.strip()

    # 2. Architecture — C4 L1–L3 from the Architecture Model (v0.4). When the model
    #    is unavailable (e.g. ArchSynthesis skipped), fall back to the v0.3 module view.
    model = state.get("architecture_model") or {}
    model_hash = state.get("model_hash", "")
    project_name = state.get("display_name") or os.path.basename(project_path)
    if model.get("components"):
        docs["02_architecture.md"] = render_architecture(model, project_name, model_hash)
        # v0.4 architecture-reconstruction docs (09–12), all from the same model.
        docs["09_deployment_infra.md"] = render_deployment(model, model_hash)
        docs["10_architecture_decisions.md"] = render_adrs(model, model_hash)
        docs["11_quality_hotspots.md"] = render_quality(model, model_hash)
        docs["12_external_integrations.md"] = render_external(model, model_hash)
    else:
        arch_md = ["# Architecture\n", "## Component Diagram\n", "```mermaid"]
        arch_md.append(render_module_diagram(state.get("modules", [])))
        arch_md.append("```\n")
        arch_md.append("## Modules\n")
        for m in state.get("modules", []):
            arch_md.append(f"### {m.get('name')}")
            if m.get("purpose"):
                arch_md.append(m["purpose"])
            files = m.get("files", [])
            if files:
                arch_md.append("\n**Files:**")
                for f in files[:50]:
                    arch_md.append(f"- `{f}`")
            arch_md.append("")
        docs["02_architecture.md"] = "\n".join(arch_md)

    # 3. Data model (ER diagram from extracted entities)
    entities = state.get("data_entities", []) or []
    dm_md = ["# Data Model\n"]
    if entities:
        dm_md.append("```mermaid")
        dm_md.append(render_er_diagram(entities))
        dm_md.append("```\n")
        for e in entities:
            dm_md.append(f"### {e['name']}")
            for f in e.get("fields", []):
                dm_md.append(f"- `{f.get('name')}` ({f.get('type', 'unknown')})")
            dm_md.append("")
    else:
        dm_md.append("_No data entities detected from the indexed code._")
    docs["03_data_model.md"] = "\n".join(dm_md)

    # 4. Flows + call graph
    flows = state.get("flows", []) or []
    call_graph = state.get("call_graph", {"edges": []})
    flows_md = ["# Flows\n", "## Overall Call Graph\n", "```mermaid"]
    flows_md.append(render_call_graph(call_graph))
    flows_md.append("```\n")
    flows_md.append("## Per-Flow Description\n")
    for f in flows:
        flows_md.append(f"### {f.get('name')}")
        flows_md.append(f"**Entry point:** `{f.get('entry_point')}`\n")
        for step in f.get("steps", []):
            flows_md.append(f"- {step}")
        flows_md.append("")
    docs["04_flows.md"] = "\n".join(flows_md)

    # 5. Sequence diagrams (one per flow)
    seq_md = ["# Sequence Diagrams\n"]
    for f in flows:
        seq_md.append(f"## {f.get('name')}\n")
        seq_md.append("```mermaid")
        seq_md.append(render_sequence_diagram(f))
        seq_md.append("```\n")
    docs["05_sequence_diagrams.md"] = "\n".join(seq_md)

    # 6. Business logic — cross-file rules grouped by flow (preferred), with a fallback
    #    to the per-file rule table when synthesis is unavailable (e.g. LLM skipped).
    docs["06_business_logic.md"] = _render_business_logic(
        state.get("business_logic") or [],
        state.get("file_summaries") or {},
    )

    # 7. API surface (endpoints + DTO catalog + sample requests)
    docs["07_api_surface.md"] = _render_api_surface(
        state.get("api_endpoints") or [],
        state.get("dto_classes") or [],
    )

    # 8. Batch jobs and scheduled tasks
    docs["08_batch_jobs.md"] = _render_batch_jobs(state.get("batch_jobs") or [])

    # --- v0.5 docs (13–16): rendered from skippable-node outputs in state. Each
    #     carries a "not configured" note when its producing node was bypassed. ---
    from ..tools.v05_docs import (
        render_dependencies,
        render_onboarding,
        render_requirements,
        render_change_digest,
    )
    docs["13_dependencies.md"] = render_dependencies(state.get("dependency_findings") or {})
    docs["14_onboarding.md"] = render_onboarding(model, state.get("eval_results") or {})
    docs["15_requirements_traceability.md"] = render_requirements(
        state.get("requirements") or [], state.get("traceability") or {},
        state.get("requirements_areapath"), state.get("trace_eval") or {},
    )
    docs["16_change_digest.md"] = render_change_digest(state.get("drift_digest") or "")

    # v0.2: return documents in memory keyed by doc_id (filename stem, no ".md").
    # The persist node stores these in Postgres (generated_docs) and embeds them
    # into Chroma (docs_<pid>). No files are written to disk.
    generated_docs: dict[str, str] = {
        name[:-3] if name.endswith(".md") else name: content
        for name, content in docs.items()
    }

    logger.info("doc_gen_done", docs=len(generated_docs))
    return {"generated_docs": generated_docs}


def _render_business_logic(business_logic: list[dict], file_summaries: dict) -> str:
    """Cross-file business logic grouped by flow, with linked file evidence.

    Falls back to the legacy per-file rule table when no synthesized cross-file logic
    is available (preserves behavior when the cross_file LLM was unavailable)."""
    lines = ["# Business Logic\n"]

    if business_logic:
        lines.append(
            "Business rules synthesized across files and tied to the flow that exercises "
            "them. Each rule links the collaborating files and cites `file:line` evidence.\n"
        )
        by_flow: dict[str, list[dict]] = {}
        for r in business_logic:
            by_flow.setdefault(r.get("flow") or "General", []).append(r)
        for flow, rules in by_flow.items():
            lines.append(f"## {flow}\n")
            for r in rules:
                lines.append(f"- **{r.get('rule', '').strip()}**")
                files = r.get("files") or []
                if files:
                    lines.append(f"  - Files: {', '.join(f'`{f}`' for f in files)}")
                evidence = r.get("evidence") or []
                if evidence:
                    ev_links = ", ".join(
                        f"[{e}]({e.split(':')[0]}#L{e.split(':')[1]})" if ":" in e else f"`{e}`"
                        for e in evidence
                    )
                    lines.append(f"  - Evidence: {ev_links}")
            lines.append("")
        return "\n".join(lines)

    # Fallback: per-file rule table (legacy behavior).
    lines += [
        "_Cross-file synthesis unavailable; showing per-file rules._\n",
        "| Rule | File | Lines | Method |",
        "|------|------|-------|--------|",
    ]
    for path, s in file_summaries.items():
        for r in s.get("business_rules", []):
            line_range = r.get("cited_lines", [0, 0])
            lines.append(
                f"| {r.get('description', '').replace('|', '/')} "
                f"| `{r.get('cited_file', path)}` "
                f"| {line_range[0]}-{line_range[1]} "
                f"| `{r.get('cited_method', '')}` |"
            )
    return "\n".join(lines)


def _render_batch_jobs(jobs: list[dict]) -> str:
    lines = ["# Batch Jobs & Scheduled Tasks\n"]

    if not jobs:
        lines.append("_No scheduled tasks or batch jobs detected in the indexed code._")
        return "\n".join(lines)

    # Summary table
    lines += [
        "## Summary\n",
        "| Job / Task | Framework | Trigger | Schedule | File |",
        "|-----------|-----------|---------|----------|------|",
    ]
    for j in jobs:
        schedule_human = j.get("schedule_human") or j.get("schedule") or "—"
        file_ref = j.get("file", "")
        line_ref = j.get("line", "")
        trigger = j.get("trigger_type", "—")
        lines.append(
            f"| `{j.get('name', '')}` "
            f"| {j.get('framework', '')} "
            f"| {trigger} "
            f"| {schedule_human} "
            f"| `{file_ref}:{line_ref}` |"
        )
    lines.append("")

    # Per-job detail
    lines.append("## Job Details\n")
    for j in jobs:
        name = j.get("name", "")
        lines.append(f"### `{name}`\n")

        desc = j.get("description")
        if desc:
            lines.append(f"{desc}\n")

        file_ref = j.get("file", "")
        line_ref = j.get("line", "")
        lines.append(f"- **Handler:** `{j.get('handler_class', '')}.{j.get('handler_method', '')}` "
                     f"([{file_ref}:{line_ref}]({file_ref}#{line_ref}))")
        lines.append(f"- **Framework:** {j.get('framework', '')}")
        lines.append(f"- **Kind:** {j.get('kind', '')}")

        schedule_human = j.get("schedule_human") or j.get("schedule") or "—"
        schedule_raw = j.get("schedule", "")
        if schedule_raw and schedule_raw != schedule_human:
            lines.append(f"- **Schedule:** {schedule_human} (`{schedule_raw}`)")
        else:
            lines.append(f"- **Schedule:** {schedule_human}")

        if j.get("role"):
            lines.append(f"- **Role:** {', '.join(j['role'])}")

        if j.get("data_read"):
            lines.append(f"- **Reads:** {', '.join(j['data_read'])}")

        if j.get("data_write"):
            lines.append(f"- **Writes:** {', '.join(j['data_write'])}")

        if j.get("error_handling"):
            lines.append(f"- **Error handling:** {j['error_handling']}")

        if j.get("estimated_duration"):
            lines.append(f"- **Estimated duration:** {j['estimated_duration']}")

        if j.get("dependencies"):
            lines.append(f"- **Dependencies:** `{'`, `'.join(j['dependencies'])}`")

        lines.append("")

    return "\n".join(lines)


def _render_api_surface(endpoints: list[dict], dtos: list[dict]) -> str:
    lines = ["# API Surface\n"]

    if not endpoints and not dtos:
        lines.append("_No REST endpoints or DTO classes detected in the indexed code._")
        return "\n".join(lines)

    # --- Endpoint table ---
    if endpoints:
        lines += [
            "## Endpoints\n",
            "| Method | Path | Handler | Auth | Request DTO | Response DTO | Status Codes |",
            "|--------|------|---------|------|-------------|--------------|--------------|",
        ]
        for ep in endpoints:
            auth_str = ", ".join(ep.get("auth") or []) or "Public"
            status_str = ", ".join(str(s) for s in (ep.get("status_codes") or []))
            lines.append(
                f"| **{ep.get('http_method', '')}** "
                f"| `{ep.get('path', '')}` "
                f"| `{ep.get('handler', ep.get('handler_class', '') + '.' + ep.get('handler_method', ''))}` "
                f"| {auth_str} "
                f"| `{ep.get('request_dto') or '—'}` "
                f"| `{ep.get('response_dto') or '—'}` "
                f"| {status_str} |"
            )
        lines.append("")

        # --- Per-endpoint detail with sample payloads ---
        lines.append("## Endpoint Details\n")
        for ep in endpoints:
            method = ep.get("http_method", "")
            path = ep.get("path", "")
            lines.append(f"### {method} {path}\n")

            desc = ep.get("description", "")
            if desc:
                lines.append(f"{desc}\n")

            handler = ep.get("handler") or (
                ep.get("handler_class", "") + "." + ep.get("handler_method", "")
            )
            lines.append(f"- **Handler:** `{handler}` ([{ep.get('file', '')}:{ep.get('line', '')}]({ep.get('file', '')}#{ep.get('line', '')}))")

            auth = ep.get("auth") or []
            lines.append(f"- **Auth:** {', '.join(auth) if auth else 'Public'}")

            if ep.get("path_variables"):
                lines.append(f"- **Path variables:** `{'`, `'.join(ep['path_variables'])}`")

            if ep.get("request_params"):
                lines.append(f"- **Query params:** `{'`, `'.join(ep['request_params'])}`")

            lines.append(f"- **Request DTO:** `{ep.get('request_dto') or '—'}`")
            lines.append(f"- **Response DTO:** `{ep.get('response_dto') or '—'}`")

            status_codes = ep.get("status_codes") or []
            if status_codes:
                lines.append(f"- **Status codes:** {', '.join(str(s) for s in status_codes)}")

            if ep.get("sample_request"):
                lines.append("\n**Sample request:**\n```json")
                lines.append(json.dumps(ep["sample_request"], indent=2))
                lines.append("```")

            if ep.get("sample_response"):
                lines.append("\n**Sample response:**\n```json")
                lines.append(json.dumps(ep["sample_response"], indent=2))
                lines.append("```")

            lines.append("")

    # --- DTO catalog ---
    if dtos:
        lines.append("## DTO Catalog\n")
        for dto in dtos:
            name = dto.get("name", "")
            file_ref = dto.get("file", "")
            line_ref = dto.get("line", "")
            kind = "TypeScript interface" if dto.get("ts_interface") else "Java class"
            used_req = "yes" if dto.get("used_as_request_body") else "no"
            used_resp = "yes" if dto.get("used_as_response_body") else "no"
            lines.append(f"### `{name}`\n")
            lines.append(
                f"[{file_ref}:{line_ref}]({file_ref}#{line_ref}) · {kind} · "
                f"request body: {used_req} · response body: {used_resp}\n"
            )
            fields = dto.get("fields") or []
            if fields:
                lines += [
                    "| Field | Type | Required | Validation |",
                    "|-------|------|----------|------------|",
                ]
                for f in fields:
                    val = ", ".join(f.get("validation") or []) or "—"
                    req = "✓" if f.get("required", True) else ""
                    lines.append(
                        f"| `{f.get('name', '')}` | `{f.get('type', '')}` | {req} | {val} |"
                    )
            lines.append("")

    return "\n".join(lines)


