"""Tool registry + dispatch for the investigation loop (§9.7).

The investigator's planner names a tool and supplies args as JSON; the loop looks
the tool up here, executes it, and records the returned string as an Observation.
Each wrapper has the uniform signature ``async (project_id, args, ctx) -> str`` so
the dispatcher stays trivial. ``available_tools`` filters the table by config
toggles and by batch mode (live/interactive tools and, by default, git/callgraph
are off for CSV runs — §9.14).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import architecture, codebase, git_tools, history, probes, rag

ToolFn = Callable[[str, dict, dict], Awaitable[str]]


# --- wrappers ---------------------------------------------------------------

async def _search_code_docs(project_id: str, args: dict, ctx: dict) -> str:
    hits = rag.search_code_docs(project_id, str(args.get("query", "")), n_results=6)
    if not hits:
        return "(no documentation/code snippets retrieved)"
    out = []
    for h in hits:
        out.append(f"[{h['collection']}] {h['relative_path']} (score={h['score']:.2f})")
        out.append(h["snippet"][:600])
        out.append("")
    return "\n".join(out)


async def _get_doc(project_id: str, args: dict, ctx: dict) -> str:
    doc = await rag.get_doc(project_id, str(args.get("doc_id", "")))
    return doc[:3000] if doc else f"(document {args.get('doc_id')} not found)"


async def _fetch_code_snippet(project_id: str, args: dict, ctx: dict) -> str:
    return (
        await rag.fetch_code_snippet(
            project_id,
            str(args.get("file", "")),
            int(args.get("start_line", 1) or 1),
            int(args.get("end_line", args.get("start_line", 1) or 1)),
        )
        or f"(could not read {args.get('file')})"
    )


async def _get_business_rules(project_id: str, args: dict, ctx: dict) -> str:
    rules = await rag.get_business_rules(project_id, str(args.get("file", "")))
    if not rules:
        return f"(no business rules recorded for {args.get('file')})"
    out = [f"Business rules for {args.get('file')}:"]
    for r in rules[:20]:
        cl = r.get("cited_lines") or [0, 0]
        out.append(f"  - {r.get('description', '')} ({r.get('cited_file', '')}:{cl[0]}-{cl[1]})")
    return "\n".join(out)


async def _get_call_graph(project_id: str, args: dict, ctx: dict) -> str:
    return await codebase.get_call_graph(project_id, str(args.get("symbol", "")))


async def _get_flow(project_id: str, args: dict, ctx: dict) -> str:
    return await codebase.get_flow(project_id, str(args.get("entry_point", "")))


async def _grep_code(project_id: str, args: dict, ctx: dict) -> str:
    res = await codebase.grep_code(
        project_id, str(args.get("pattern", "")), max_results=int(args.get("max_results", 30))
    )
    return res or "(no matches)"


async def _git_blame(project_id: str, args: dict, ctx: dict) -> str:
    return await git_tools.git_blame(
        project_id,
        str(args.get("file", "")),
        int(args.get("start_line", 1) or 1),
        int(args.get("end_line", args.get("start_line", 1) or 1)),
    )


async def _git_log_recent(project_id: str, args: dict, ctx: dict) -> str:
    return await git_tools.git_log_recent(
        project_id, args.get("path"), max_count=int(args.get("max_count", 10))
    )


async def _find_similar_issues(project_id: str, args: dict, ctx: dict) -> str:
    facts = ctx.get("facts") or {}
    return await history.find_similar_issues(
        project_id,
        str(args.get("signature") or facts.get("error_signature", "")),
        exception_type=facts.get("exception_type"),
        exclude_conversation_id=ctx.get("conversation_id"),
    )


# --- v0.4: architecture-model + runtime-probe tools -------------------------

async def _get_architecture(project_id: str, args: dict, ctx: dict) -> str:
    return await architecture.get_architecture(project_id, args.get("component"))


async def _discover_endpoints(project_id: str, args: dict, ctx: dict) -> str:
    return await architecture.discover_endpoints(project_id, args.get("component"))


async def _discover_datasources(project_id: str, args: dict, ctx: dict) -> str:
    return await architecture.discover_datasources(project_id)


# --- registry ---------------------------------------------------------------

_REGISTRY: dict[str, dict[str, Any]] = {
    "search_code_docs": {
        "fn": _search_code_docs,
        "args": {"query": "natural-language search over docs + code summaries"},
        "desc": "Similarity search across docs_<pid> + code_<pid>. Use to learn what the area is supposed to do.",
        "batch": True,
    },
    "get_doc": {
        "fn": _get_doc,
        "args": {"doc_id": "e.g. 04_flows, 06_business_logic, 02_architecture"},
        "desc": "Fetch a full generated document for the affected area.",
        "batch": True,
    },
    "fetch_code_snippet": {
        "fn": _fetch_code_snippet,
        "args": {"file": "relative path or basename", "start_line": "int", "end_line": "int"},
        "desc": "Read the actual source at a cited location (the failing line + context).",
        "batch": True,
    },
    "get_business_rules": {
        "fn": _get_business_rules,
        "args": {"file": "relative path or basename"},
        "desc": "Persisted business rules + edge cases for a file (Agent #1 output).",
        "batch": True,
    },
    "get_call_graph": {
        "fn": _get_call_graph,
        "args": {"symbol": "method/function name, e.g. OrderService.price"},
        "desc": "Declarations + textual references (candidate callers/callees) of a symbol.",
        "batch": False,
    },
    "get_flow": {
        "fn": _get_flow,
        "args": {"entry_point": "endpoint/use-case, e.g. checkout"},
        "desc": "The traced entry→DB flow for the affected path (from 04_flows).",
        "batch": True,
    },
    "grep_code": {
        "fn": _grep_code,
        "args": {"pattern": "literal or regex", "max_results": "int (optional)"},
        "desc": "Search the repo for a symbol or string — file:line matches.",
        "batch": False,
    },
    "git_blame": {
        "fn": _git_blame,
        "args": {"file": "relative path or basename", "start_line": "int", "end_line": "int"},
        "desc": "Last change + author + commit for suspect lines (regression hunting).",
        "batch": False,
    },
    "git_log_recent": {
        "fn": _git_log_recent,
        "args": {"path": "relative path (optional)", "max_count": "int (optional)"},
        "desc": "Recent commits touching the area — did something change recently?",
        "batch": False,
    },
    "find_similar_issues": {
        "fn": _find_similar_issues,
        "args": {"signature": "error signature (optional; defaults to this issue's)"},
        "desc": "Prior triaged issues with the same signature + their confirmed verdicts.",
        "batch": True,
    },
    # v0.4 — architecture-model discovery (read-only; safe in batch).
    "get_architecture": {
        "fn": _get_architecture,
        "args": {"component": "component name (optional)"},
        "desc": "Query the Architecture Model — components, connectors, datastores.",
        "batch": True,
    },
    "discover_endpoints": {
        "fn": _discover_endpoints,
        "args": {"component": "component name (optional)"},
        "desc": "List callable endpoints (method, path, controller) — for http_probe construction.",
        "batch": True,
    },
    "discover_datasources": {
        "fn": _discover_datasources,
        "args": {},
        "desc": "List datastores + entities + DSN env-var names — for db_query construction.",
        "batch": True,
    },
    # v0.4 — live read-only runtime probes (interactive only; never in batch — §9.14).
    "http_probe": {
        "fn": probes.http_probe_tool,
        "args": {"target": "target name from discover_endpoints", "environment": "dev|test|prod",
                 "method": "GET|HEAD", "path": "/orders/123", "params": "dict (optional)"},
        "desc": "Live, read-only API call against a resolved target — observe the actual failure.",
        "batch": False,
    },
    "db_query": {
        "fn": probes.db_query_tool,
        "args": {"target": "target name from discover_datasources", "environment": "dev|test|prod",
                 "sql": "a single SELECT/EXPLAIN"},
        "desc": "Live, read-only SQL against a resolved target — check the actual data the code reads.",
        "batch": False,
    },
}


def available_tools(config: dict, *, batch: bool = False) -> dict[str, ToolFn]:
    """Return {name: fn} enabled for this run.

    Per-tool config toggles live under ``sre.tools`` (default on). In batch mode
    only tools flagged ``batch`` are offered — no git/callgraph/grep inside a
    500-row CSV run (§9.14).
    """
    toggles = (config.get("sre", {}) or {}).get("tools", {}) or {}
    out: dict[str, ToolFn] = {}
    for name, spec in _REGISTRY.items():
        if toggles.get(name, True) is False:
            continue
        if batch and not spec["batch"]:
            continue
        out[name] = spec["fn"]
    return out


def tool_catalog(names: list[str]) -> str:
    """Render the prompt-facing catalog (name, description, args) for given tools."""
    lines = []
    for name in names:
        spec = _REGISTRY[name]
        arg_str = ", ".join(f"{k} ({v})" for k, v in spec["args"].items()) or "(none)"
        lines.append(f"- {name}: {spec['desc']}\n    args: {arg_str}")
    return "\n".join(lines)
