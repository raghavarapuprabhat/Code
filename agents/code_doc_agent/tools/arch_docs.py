"""Deterministic renderers for the v0.4 architecture documents (§8.8.3).

All five docs (02_architecture C4, 09_deployment_infra, 10_architecture_decisions,
11_quality_hotspots, 12_external_integrations) are rendered from the same
ArchitectureModel so they are consistent by construction. Each doc carries a
`model_hash` staleness stamp (§8.8.5) at the bottom.
"""
from __future__ import annotations

from .mermaid_tools import _safe_id


def _stamp(model_hash: str) -> str:
    short = (model_hash or "")[:12]
    return f"\n\n---\n*Rendered from Architecture Model `{short}`.*\n"


# --- 02_architecture (C4 L1–L3) --------------------------------------------

def render_architecture(model: dict, project_name: str, model_hash: str) -> str:
    components = model.get("components", [])
    connectors = model.get("connectors", [])
    external = model.get("external_systems", [])
    datastores = model.get("datastores", [])

    out = [f"# Architecture — {project_name}\n",
           "Generated from the machine-readable Architecture Model. The context, container",
           "and component views below are derived from the same `components` + `connectors`,",
           "so they are consistent with each other by construction.\n"]

    # C4 L1 — System Context
    out.append("## C4 Level 1 — System Context\n")
    out.append("```mermaid")
    out.append("graph TD")
    out.append(f"  SYS[{project_name}]")
    for e in external[:12]:
        eid = _safe_id(e.get("name", "ext"))
        out.append(f"  SYS --> EXT_{eid}[{e.get('name','external')}]")
    for d in datastores[:8]:
        did = _safe_id(d.get("kind", "db"))
        out.append(f"  SYS --> DB_{did}[({d.get('kind','datastore')})]")
    out.append("```\n")

    # C4 L2 — Container / layer view
    out.append("## C4 Level 2 — Containers (Layers)\n")
    layers = model.get("layers", [])
    if layers:
        out.append("```mermaid")
        out.append("graph TD")
        for l in layers:
            lid = _safe_id(l.get("name", "layer"))
            members = ", ".join(l.get("components", [])[:6])
            out.append(f"  L_{lid}[\"{l.get('name','layer')}<br/>{members}\"]")
        out.append("```\n")
    else:
        out.append("_No distinct layers detected._\n")

    # C4 L3 — Component diagram
    out.append("## C4 Level 3 — Components\n")
    out.append("```mermaid")
    out.append("graph LR")
    for c in components[:40]:
        cid = _safe_id(c.get("name", "comp"))
        out.append(f"  {cid}[\"{c.get('name','')}<br/><i>{c.get('layer','')}</i>\"]")
    seen = set()
    for cn in connectors[:80]:
        a, b = _safe_id(cn.get("from", "")), _safe_id(cn.get("to", ""))
        if (a, b) in seen or not a or not b:
            continue
        seen.add((a, b))
        out.append(f"  {a} -->|{cn.get('kind','call')}| {b}")
    out.append("```\n")

    # Component catalogue
    out.append("## Component Catalogue\n")
    for c in components:
        out.append(f"### {c.get('name','')}")
        out.append(f"- **Layer:** {c.get('layer','unknown')} · **Stereotype:** {c.get('stereotype','module')}")
        if c.get("description"):
            out.append(f"- {c['description']}")
        files = c.get("files", [])
        if files:
            out.append(f"- **Files ({len(files)}):** " + ", ".join(f"`{f}`" for f in files[:12]))
        out.append("")

    out.append(_stamp(model_hash))
    return "\n".join(out)


# --- 09_deployment_infra ----------------------------------------------------

def render_deployment(model: dict, model_hash: str) -> str:
    units = model.get("deployment_units", [])
    out = ["# Deployment & Infrastructure\n"]
    if not units:
        out.append("_No deployment manifests (Dockerfile / compose / k8s) detected._")
        out.append(_stamp(model_hash))
        return "\n".join(out)

    out += ["## Deployment Units\n",
            "| Unit | Image | Ports | Source | Depends On |",
            "|------|-------|-------|--------|------------|"]
    for u in units:
        ports = ", ".join(u.get("ports", [])) or "—"
        deps = ", ".join(u.get("depends_on", [])) or "—"
        out.append(f"| `{u.get('name','')}` | `{u.get('image') or '—'}` | {ports} "
                   f"| {u.get('source','')} | {deps} |")
    out.append("")

    # Env-var contract (names only).
    out.append("## Environment Variable Contract\n")
    all_env: set[str] = set()
    for u in units:
        all_env.update(u.get("env_vars", []))
    if all_env:
        out.append("These environment variables are referenced by the deployment units "
                   "(names only — values are never captured):\n")
        for ev in sorted(all_env):
            out.append(f"- `{ev}`")
    else:
        out.append("_No environment variables declared in deployment manifests._")
    out.append("")

    # Service dependency graph.
    if any(u.get("depends_on") for u in units):
        out += ["## Service Dependency Graph\n", "```mermaid", "graph TD"]
        for u in units:
            uid = _safe_id(u.get("name", ""))
            for dep in u.get("depends_on", []):
                out.append(f"  {uid} --> {_safe_id(dep)}")
        out.append("```\n")

    out.append(_stamp(model_hash))
    return "\n".join(out)


# --- 10_architecture_decisions ---------------------------------------------

def render_adrs(model: dict, model_hash: str) -> str:
    decisions = model.get("decisions", [])
    out = ["# Architecture Decisions (Inferred)\n",
           "> These ADRs are **inferred from code, config and commit evidence** — they were",
           "> not written by the original authors. Confirm or correct them; low-confidence",
           "> inferences are marked ⚠ unverified.\n"]
    if not decisions:
        out.append("_No architecture decisions could be confidently inferred._")
        out.append(_stamp(model_hash))
        return "\n".join(out)

    for i, d in enumerate(decisions, 1):
        flag = " ⚠ *unverified*" if d.get("unverified") else ""
        out.append(f"## ADR-{i:03d}: {d.get('title','(untitled)')}{flag}")
        out.append(f"**Confidence:** {d.get('confidence','medium')}\n")
        out.append(f"**Decision:** {d.get('decision','')}\n")
        ev = d.get("evidence", [])
        if ev:
            out.append("**Evidence:**")
            for e in ev:
                out.append(f"- `{e}`")
            out.append("")
        if d.get("rationale"):
            out.append(f"**Rationale (inferred):** {d['rationale']}\n")
        if d.get("consequences"):
            out.append(f"**Consequences:** {d['consequences']}\n")
    out.append(_stamp(model_hash))
    return "\n".join(out)


# --- 11_quality_hotspots ----------------------------------------------------

def render_quality(model: dict, model_hash: str) -> str:
    q = model.get("quality", {}) or {}
    out = ["# Quality & Hotspots\n",
           "Where the next bug is most likely to come from — churn × complexity, cyclic",
           "dependencies, layer violations and dead code. Consumed by the SRE Agent as",
           "investigation priors.\n"]

    hotspots = q.get("hotspots", [])
    out.append("## Hotspot Matrix (churn × complexity)\n")
    if hotspots:
        out += ["| Score | File | Churn | Complexity |",
                "|-------|------|-------|------------|"]
        for h in hotspots:
            out.append(f"| {h.get('score',0):.2f} | `{h.get('file','')}` "
                       f"| {h.get('churn',0)} | {h.get('complexity',0)} |")
    else:
        out.append("_No hotspots computed (no git history or complexity signal)._")
    out.append("")

    cyclic = q.get("cyclic_dependencies", [])
    out.append("## Cyclic Dependencies\n")
    if cyclic:
        for cyc in cyclic:
            out.append(f"- {' → '.join(cyc)} → {cyc[0]}")
    else:
        out.append("_No component cycles detected._")
    out.append("")

    violations = q.get("layer_violations", [])
    out.append("## Layer Violations\n")
    if violations:
        for v in violations:
            out.append(f"- {v}")
    else:
        out.append("_No layer violations detected._")
    out.append("")

    dead = q.get("dead_code", [])
    if dead:
        out.append("## Potentially Dead Code\n")
        out.append("_Public types not referenced by any import (best-effort; verify before removing)._\n")
        for d in dead[:20]:
            out.append(f"- `{d}`")
        out.append("")

    oversized = q.get("oversized_files", [])
    if oversized:
        out.append("## Oversized Files\n")
        for o in oversized:
            out.append(f"- `{o}`")
        out.append("")

    todos = q.get("todo_density", {})
    if todos:
        out.append("## TODO / FIXME Density\n")
        out += ["| File | Count |", "|------|-------|"]
        for f, n in todos.items():
            out.append(f"| `{f}` | {n} |")
        out.append("")

    out.append(_stamp(model_hash))
    return "\n".join(out)


# --- 12_external_integrations ----------------------------------------------

def render_external(model: dict, model_hash: str) -> str:
    external = model.get("external_systems", [])
    out = ["# External Integrations\n",
           "Outbound HTTP clients, queues and third-party SDKs. Doubles as the probe-target",
           "discovery source for the SRE Agent (§9.7A) — base-URL config keys only, never",
           "secret values.\n"]
    if not external:
        out.append("_No outbound integrations detected from config/code._")
        out.append(_stamp(model_hash))
        return "\n".join(out)

    out += ["| System | Kind | Base-URL Config Key | Auth | Calling Components |",
            "|--------|------|---------------------|------|--------------------|"]
    for e in external:
        comps = ", ".join(e.get("calling_components", [])) or "—"
        out.append(f"| {e.get('name','')} | {e.get('kind','http')} "
                   f"| `{e.get('base_url_config_key') or '—'}` | {e.get('auth_style') or '—'} | {comps} |")
    out.append("")

    out.append(_stamp(model_hash))
    return "\n".join(out)
