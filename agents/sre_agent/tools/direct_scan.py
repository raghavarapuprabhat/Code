"""Direct deterministic project scan — discovery tier (b) (§9.7A, v0.7).

When no `architecture_models` row exists (project never indexed, or indexed pre-v0.4),
the `discover_*` tools fall back to a standalone, on-demand parse of the project root
instead of dead-ending into asking the user. This reuses the SAME parsers the code-doc
ConfigInfraScan node uses (datasources/external systems) plus a deterministic endpoint
scan (controller annotations / route literals), so the SRE Agent has no hard dependency
on Agent #1 having indexed the project.

Results are cached per project root for the lifetime of the process (a conversation is
short-lived), so repeated discover_* calls in one investigation don't re-walk the tree.

The Evidence/observation records which tier resolved the target via `discovered_from`:
  "architecture_model" | "direct_scan" | "user"
"""
from __future__ import annotations

import os
import re

import structlog

logger = structlog.get_logger()

_MAX_FILES = 4000
_MAX_FILE_BYTES = 200_000
_IGNORE_DIRS = {"node_modules", "target", "build", "dist", ".git", ".venv", "__pycache__"}
_CODE_EXTS = (".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".py")

# Endpoint detection patterns (deterministic; no LLM).
# Method-level mappings only (the verb-specific annotations). Class-level
# @RequestMapping is handled separately as a base path, never as its own endpoint.
_JAVA_MAPPING = re.compile(
    r'@(Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"'
)
_JAVA_CLASS_MAPPING = re.compile(r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"')
_JS_ROUTE = re.compile(
    r'\.(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]', re.IGNORECASE
)
_PY_ROUTE = re.compile(
    r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]', re.IGNORECASE
)

# Cache: project_root -> {"endpoints": [...], "datasources": [...], "external": [...]}
_CACHE: dict[str, dict] = {}


def _read(full: str) -> str:
    try:
        with open(full, errors="replace") as fh:
            return fh.read(_MAX_FILE_BYTES)
    except OSError:
        return ""


def _scan_endpoints(root: str) -> list[dict]:
    """Controller annotations (Java) + route literals (Express/FastAPI) → endpoints."""
    endpoints: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(_CODE_EXTS):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            content = _read(os.path.join(dirpath, fn))
            if not content:
                continue
            base_path = ""
            cm = _JAVA_CLASS_MAPPING.search(content)
            if cm and "@RestController" in content:
                base_path = cm.group(1)
            for m in _JAVA_MAPPING.finditer(content):
                verb = m.group(1).upper()
                method = "GET" if verb == "REQUEST" else verb
                line = content[: m.start()].count("\n") + 1
                endpoints.append({
                    "method": method,
                    "path": (base_path + m.group(2)) or m.group(2),
                    "file": f"{rel}:{line}",
                })
            for pat in (_JS_ROUTE, _PY_ROUTE):
                for m in pat.finditer(content):
                    line = content[: m.start()].count("\n") + 1
                    endpoints.append({
                        "method": m.group(1).upper(),
                        "path": m.group(2),
                        "file": f"{rel}:{line}",
                    })
            if len(endpoints) > 200:
                return endpoints[:200]
    return endpoints


def _scan_datasources(root: str) -> tuple[list[dict], list[dict]]:
    """Reuse the code-doc ConfigInfraScan parsers for datasource + external discovery."""
    try:
        from agents.code_doc_agent.nodes.config_infra import (
            _CONFIG_GLOBS, _scan_config_text,
        )
    except Exception:  # noqa: BLE001 — code-doc agent not importable; degrade
        return [], []

    datasources: list[dict] = []
    external: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        depth = os.path.relpath(dirpath, root).count(os.sep)
        if depth > 4:
            dirnames[:] = []
            continue
        for fn in filenames:
            low = fn.lower()
            if low in _CONFIG_GLOBS or low.endswith((".env.example", ".env.sample")):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                ds, ext = _scan_config_text(_read(os.path.join(dirpath, fn)))
                for d in ds:
                    d["discovered_from"] = "direct_scan"
                    d["source_file"] = rel
                    datasources.append(d)
                for e in ext:
                    e["discovered_from"] = "direct_scan"
                    external.append(e)
    return datasources, external


def scan_project(root: str) -> dict:
    """Run (or return cached) deterministic discovery for a project root."""
    if root in _CACHE:
        return _CACHE[root]
    if not root or not os.path.isdir(root):
        return {"endpoints": [], "datasources": [], "external": []}
    endpoints = _scan_endpoints(root)
    datasources, external = _scan_datasources(root)
    result = {"endpoints": endpoints, "datasources": datasources, "external": external}
    _CACHE[root] = result
    logger.info(
        "direct_scan_done",
        root=root, endpoints=len(endpoints), datasources=len(datasources),
    )
    return result


def clear_cache(root: str | None = None) -> None:
    if root is None:
        _CACHE.clear()
    else:
        _CACHE.pop(root, None)
