"""Phase 6c — QualityScan (§8.8.2/8.8.3) + InferredADR (§8.8.4).

Deterministic quality signals (no LLM):
  - hotspot matrix: git churn × complexity proxy, top-N risky files
  - cyclic dependencies: NetworkX strongly-connected components over the component graph
  - layer violations: from the Architecture Model layers
  - dead code: public methods with no inbound reference (best-effort from imports)
  - oversized files: LOC over threshold
  - TODO/FIXME density

ADR inference is the one LLM call here (low cost) — it reads the assembled evidence and
writes honestly-labelled inferred decisions.

Both outputs are merged back into `architecture_model` (quality + decisions).
"""
from __future__ import annotations

import json
import os
import re

import structlog

from shared.llm_adapter import build_adapter_from_config
from ..state import CodeDocState

logger = structlog.get_logger()

ADR_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "infer_adr.md")

_HOTSPOT_TOP_N = 12
_OVERSIZED_LOC = 600
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")


def _load(path: str) -> str:
    with open(path) as fh:
        return fh.read()


def _git_churn(repo_path: str) -> dict[str, int]:
    """commits-touching-file count per relative path. Empty if not a git repo."""
    try:
        from git import InvalidGitRepositoryError, NoSuchPathError, Repo
    except ImportError:
        return {}
    try:
        repo = Repo(repo_path, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError, Exception):  # noqa: BLE001
        return {}
    churn: dict[str, int] = {}
    try:
        # Last 500 commits is plenty for a hotspot signal.
        for commit in repo.iter_commits(max_count=500):
            for path in commit.stats.files:
                churn[path] = churn.get(path, 0) + 1
    except Exception:  # noqa: BLE001
        return churn
    return churn


def _complexity(ast: dict) -> int:
    """Cheap complexity proxy: method count + total method span lines / 20."""
    methods = 0
    span = 0
    for cls in ast.get("classes", []):
        for m in cls.get("methods", []):
            methods += 1
            span += max(0, m.get("end_line", 0) - m.get("start_line", 0))
    for fn in ast.get("functions", []):
        methods += 1
        span += max(0, fn.get("end_line", 0) - fn.get("start_line", 0))
    return methods + span // 20


def _hotspots(asts: dict[str, dict], inventory: list[dict], churn: dict[str, int]) -> list[dict]:
    rows = []
    for meta in inventory:
        rel = meta.get("relative_path", "")
        ast = asts.get(rel, {})
        cx = _complexity(ast)
        # churn keys are repo-relative; try exact + basename match.
        ch = churn.get(rel) or churn.get(os.path.basename(rel)) or 0
        if cx == 0 and ch == 0:
            continue
        raw = ch * (cx or 1)
        rows.append({"file": rel, "churn": ch, "complexity": cx, "raw": raw})
    if not rows:
        return []
    max_raw = max(r["raw"] for r in rows) or 1
    for r in rows:
        r["score"] = round(r["raw"] / max_raw, 3)
        r["reason"] = f"churn={r['churn']} × complexity={r['complexity']}"
        del r["raw"]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:_HOTSPOT_TOP_N]


def _cyclic_dependencies(model: dict) -> list[list[str]]:
    try:
        import networkx as nx
    except ImportError:
        return []
    g = nx.DiGraph()
    for cn in model.get("connectors", []):
        g.add_edge(cn["from"], cn["to"])
    cycles = []
    for scc in nx.strongly_connected_components(g):
        if len(scc) > 1:
            cycles.append(sorted(scc))
    return cycles


def _dead_code(asts: dict[str, dict]) -> list[str]:
    """Public methods/classes never referenced by any import (best-effort)."""
    all_imports_text = ""
    declared: dict[str, str] = {}        # class name -> file:line
    for rel, ast in asts.items():
        all_imports_text += " ".join(ast.get("imports", [])) + " "
        for cls in ast.get("classes", []):
            declared[cls["name"]] = f"{rel}:{cls.get('start_line', '')}"
    dead = []
    for name, loc in declared.items():
        # crude: name not referenced anywhere in any import path
        if name not in all_imports_text and len(name) > 3:
            dead.append(f"{name} ({loc})")
    return dead[:30]


def _oversized(inventory: list[dict]) -> list[str]:
    return [
        f"{m['relative_path']} ({m.get('loc', 0)} LOC)"
        for m in inventory
        if m.get("loc", 0) > _OVERSIZED_LOC
    ][:30]


def _todo_density(project_path: str, inventory: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in inventory[:2000]:
        rel = m.get("relative_path", "")
        full = os.path.join(project_path, rel)
        try:
            with open(full, errors="replace") as fh:
                content = fh.read(120_000)
        except OSError:
            continue
        n = len(_TODO_RE.findall(content))
        if n:
            out[rel] = n
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True)[:20])


async def _infer_adrs(model: dict, config_infra: dict, project_path: str, config: dict) -> list[dict]:
    """One LLM call to infer ADRs from assembled evidence."""
    # Gather recent commit subjects for evidence.
    commits = []
    try:
        from git import Repo
        repo = Repo(project_path, search_parent_directories=True)
        commits = [f"{c.hexsha[:7]}: {c.summary}" for c in repo.iter_commits(max_count=20)]
    except Exception:  # noqa: BLE001
        pass

    notable_deps: list[str] = []
    for bd in (config_infra.get("build_deps") or {}).values():
        notable_deps.extend(list((bd.get("dependencies") or {}).keys())[:25])

    evidence = {
        "datastores": model.get("datastores", []),
        "deployment_units": [
            {k: u.get(k) for k in ("name", "image", "ports", "source")}
            for u in model.get("deployment_units", [])
        ],
        "external_systems": model.get("external_systems", []),
        "notable_dependencies": notable_deps[:40],
        "layers": [{"name": l.get("name"), "components": l.get("components", [])[:6]}
                   for l in model.get("layers", [])],
        "recent_commits": commits,
    }
    try:
        llm = build_adapter_from_config(config)
        prompt = _load(ADR_PROMPT_PATH).replace("{evidence_json}", json.dumps(evidence, indent=2)[:40_000])
        resp = await llm.chat([{"role": "user", "content": prompt}])
        parsed = _safe_json(resp.content) or {}
        return parsed.get("decisions", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("infer_adr_failed", err=str(exc))
        return []


async def quality_scan_node(state: CodeDocState, *, config: dict) -> dict:
    model = dict(state.get("architecture_model") or {})
    if not model:
        return {"architecture_model": model}

    asts = state.get("asts") or {}
    inventory = state.get("file_inventory") or []
    project_path = state["project_path"]

    churn = _git_churn(project_path)
    hotspots = _hotspots(asts, inventory, churn)
    cyclic = _cyclic_dependencies(model)
    layer_violations = [v for l in model.get("layers", []) for v in l.get("violations", [])]
    dead = _dead_code(asts)
    oversized = _oversized(inventory)
    todos = _todo_density(project_path, inventory)

    quality = {
        "hotspots": hotspots,
        "cyclic_dependencies": cyclic,
        "layer_violations": layer_violations,
        "dead_code": dead,
        "oversized_files": oversized,
        "todo_density": todos,
    }

    decisions = await _infer_adrs(model, state.get("config_infra") or {}, project_path, config)

    model["quality"] = quality
    model["decisions"] = decisions

    logger.info(
        "quality_scan_done",
        hotspots=len(hotspots),
        cyclic=len(cyclic),
        violations=len(layer_violations),
        adrs=len(decisions),
    )
    return {"architecture_model": model}


def _safe_json(text: str):
    text = (text or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
