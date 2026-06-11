"""Deterministic renderers for the v0.5 documents (§8.9.10): 13–16.

Each renderer degrades gracefully: if its producing node was skipped (no ADO areapath,
no auditor binary, no eval set), the doc carries a clear "not configured" note rather
than failing the run.
"""
from __future__ import annotations

from .mermaid_tools import _safe_id


# --- 13_dependencies (§8.9.7) ----------------------------------------------

def render_dependencies(findings: dict) -> str:
    out = ["# Dependencies & Security Posture\n"]
    if not findings or findings.get("skipped"):
        reason = (findings or {}).get("reason", "no auditor configured")
        out.append(f"> **Not configured** — dependency audit was skipped ({reason}). "
                   "Install `npm audit` / OWASP Dependency-Check and re-index to populate this doc.")
        return "\n".join(out)

    cves = findings.get("cves", [])
    out.append("## Known Vulnerabilities (CVEs)\n")
    if cves:
        out += ["| Severity | Dependency | Fixed In | Affected Components |",
                "|----------|-----------|----------|---------------------|"]
        for c in cves:
            comps = ", ".join(c.get("components", [])) or "—"
            out.append(f"| {c.get('severity','?')} | `{c.get('dependency','')}` "
                       f"| {c.get('fixed_in','—')} | {comps} |")
    else:
        out.append("_No known vulnerabilities found._")
    out.append("")

    licenses = findings.get("licenses", [])
    if licenses:
        out.append("## License Inventory\n")
        out += ["| License | Count | Copyleft |", "|---------|-------|----------|"]
        for l in licenses:
            cl = "⚠ yes" if l.get("copyleft") else "no"
            out.append(f"| {l.get('license','')} | {l.get('count',0)} | {cl} |")
        out.append("")

    outdated = findings.get("outdated", [])
    if outdated:
        out.append("## Outdated Majors\n")
        for o in outdated:
            out.append(f"- `{o.get('name','')}`: {o.get('current','')} → {o.get('latest','')}")
        out.append("")
    return "\n".join(out)


# --- 14_onboarding (§8.9.8) -------------------------------------------------

def render_onboarding(model: dict, eval_results: dict) -> str:
    out = ["# Onboarding Path\n",
           "A topologically-ordered reading path through the system — start at the entry",
           "points, follow the dependencies inward to the core domain, then infrastructure.\n"]
    components = model.get("components", [])
    if not components:
        out.append("> **Not available** — no Architecture Model components to order. "
                   "Re-index after architecture reconstruction runs.")
        return "\n".join(out)

    # Order: ui/controller (entry) → service/domain → repository/infra.
    layer_order = {"ui": 0, "controller": 1, "service": 2, "domain": 3, "repository": 4, "infra": 5, "unknown": 6}
    ordered = sorted(components, key=lambda c: layer_order.get(c.get("layer", "unknown"), 6))

    questions = [q.get("question", "") for q in (eval_results.get("items") or [])]
    for i, c in enumerate(ordered, 1):
        out.append(f"## Step {i}: {c.get('name','')}  _({c.get('layer','')})_\n")
        if c.get("description"):
            out.append(f"{c['description']}\n")
        files = c.get("files", [])
        if files:
            out.append("**Key files:** " + ", ".join(f"`{f}`" for f in files[:3]))
        q = questions[i - 1] if i - 1 < len(questions) else None
        if q:
            out.append(f"\n*After this step you should be able to answer:* {q}")
        out.append("")
    return "\n".join(out)


# --- 15_requirements_traceability (§8.9.1) ---------------------------------

def render_requirements(requirements: list[dict], traceability: dict,
                        areapath: str | None) -> str:
    out = ["# Requirements Traceability\n"]
    if not areapath:
        out.append("> **Not configured** — no ADO requirements area path was provided at index "
                   "time. Set one via `POST /agents/code_doc/projects/{id}/requirements` to link "
                   "work items ⟷ components ⟷ business rules ⟷ tests.")
        return "\n".join(out)
    if not requirements:
        out.append(f"> Area path `{areapath}` was set but no work items were ingested "
                   "(ADO MCP unavailable or area empty).")
        return "\n".join(out)

    rows = traceability.get("matrix", [])
    out.append(f"Requirements sourced from ADO area path `{areapath}`.\n")
    out += ["| Work Item | Type | State | Status | Components | Tests |",
            "|-----------|------|-------|--------|-----------|-------|"]
    for r in rows:
        comps = ", ".join(r.get("components", [])) or "—"
        tests = ", ".join(r.get("tests", [])) or "—"
        wi = r.get("work_item_id", "")
        out.append(f"| [#{wi}](wi:{wi}) | {r.get('wi_type','')} | {r.get('state','')} "
                   f"| {r.get('status','')} | {comps} | {tests} |")
    out.append("")

    unimplemented = [r for r in rows if r.get("status") == "unimplemented"]
    if unimplemented:
        out.append("## ⚠ Unimplemented Requirements\n")
        for r in unimplemented:
            out.append(f"- #{r.get('work_item_id','')}: {r.get('title','')}")
        out.append("")

    untraced = traceability.get("untraced_components", [])
    if untraced:
        out.append("## Untraced Components (no linked requirement)\n")
        for c in untraced:
            out.append(f"- {c}")
        out.append("")
    return "\n".join(out)


# --- 16_change_digest (§8.9.4) ---------------------------------------------

def render_change_digest(digest_md: str) -> str:
    out = ["# Architecture Change Digest\n"]
    if not digest_md:
        out.append("> No prior Architecture Model to diff against — this is the first index, "
                   "or no changes were detected. Future re-indexes will list new/removed "
                   "components, connectors, endpoints and requirement impacts here.")
        return "\n".join(out)
    out.append(digest_md)
    return "\n".join(out)
