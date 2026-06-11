"""Phase 5a — ConfigInfraScan (§8.8.2): deterministic, no-LLM parse of build,
config and infrastructure files.

Output (`config_infra` in state) feeds ArchSynthesis (datastores, external systems,
deployment units) and the QualityScan/DependencyAudit nodes. Nothing here calls an
LLM — it is pure file parsing so the inventory is reproducible and cheap.

Parsed sources:
  build/deps      : pom.xml, build.gradle(.kts), package.json
  datasources/cfg : application.yml/.yaml/.properties, .env.example, *.env.sample
  deployment      : Dockerfile, docker-compose.yml, k8s manifests, *-pipeline.yml

Secret hygiene: we capture env-var NAMES and config KEYS only — never values. A value
that looks like a secret is replaced with «value».
"""
from __future__ import annotations

import os
import re

import structlog

from ..state import CodeDocState

logger = structlog.get_logger()

_BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "package.json"}
_CONFIG_GLOBS = (
    "application.yml", "application.yaml", "application.properties",
    ".env.example", ".env.sample", "env.example",
)
_DEPLOY_FILES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}

_MAX_FILE_BYTES = 200_000

# Datasource URL detection (keys only; we mask values).
_DSN_KEYS = re.compile(
    r"(spring\.datasource\.url|DATABASE_URL|[A-Z0-9_]*_DB_URL|[A-Z0-9_]*_DSN|"
    r"MONGO(?:DB)?_URI|REDIS_URL|datasource\.url)",
    re.IGNORECASE,
)
_DSN_KIND = [
    (re.compile(r"jdbc:postgresql|postgres", re.I), "postgres"),
    (re.compile(r"jdbc:mysql|mysql", re.I), "mysql"),
    (re.compile(r"mongodb(\+srv)?:", re.I), "mongo"),
    (re.compile(r"redis:", re.I), "redis"),
    (re.compile(r"jdbc:h2|h2:", re.I), "h2"),
    (re.compile(r"sqlite", re.I), "sqlite"),
]
# Outbound base-URL config keys (external systems).
_BASEURL_KEY = re.compile(
    r"([A-Z0-9_]*(?:_URL|_BASE_URL|_ENDPOINT|_API|_HOST)|[\w.]*base-?url)",
    re.IGNORECASE,
)


def _read(path: str) -> str:
    try:
        with open(path, errors="replace") as fh:
            return fh.read(_MAX_FILE_BYTES)
    except OSError:
        return ""


def _mask(line: str) -> str:
    """Keep the key, drop the value (= or : separator)."""
    return re.sub(r"([:=])\s*\S.*$", r"\1 «value»", line.strip())


def _parse_package_json(text: str) -> dict:
    import json
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    deps = {}
    for key in ("dependencies", "devDependencies"):
        for name, ver in (data.get(key) or {}).items():
            deps[name] = ver
    scripts = data.get("scripts") or {}
    return {"name": data.get("name"), "dependencies": deps, "scripts": scripts}


def _parse_pom(text: str) -> dict:
    deps = {}
    # <dependency><groupId>g</groupId><artifactId>a</artifactId><version>v</version>
    for m in re.finditer(
        r"<dependency>\s*<groupId>(.*?)</groupId>\s*<artifactId>(.*?)</artifactId>"
        r"(?:\s*<version>(.*?)</version>)?",
        text, re.DOTALL,
    ):
        g, a, v = m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()
        deps[f"{g}:{a}"] = v or "managed"
    return {"dependencies": deps}


def _parse_gradle(text: str) -> dict:
    deps = {}
    # implementation 'group:artifact:version'  or  implementation("group:artifact:version")
    for m in re.finditer(
        r"""(?:implementation|api|compile|runtimeOnly|testImplementation)\s*[(\s]['"]([^'"]+)['"]""",
        text,
    ):
        coord = m.group(1)
        parts = coord.split(":")
        if len(parts) >= 2:
            deps[f"{parts[0]}:{parts[1]}"] = parts[2] if len(parts) > 2 else "managed"
    return {"dependencies": deps}


def _classify_dsn(blob: str) -> str:
    for pat, kind in _DSN_KIND:
        if pat.search(blob):
            return kind
    return "unknown"


def _scan_config_text(text: str) -> tuple[list[dict], list[dict]]:
    """Return (datasources, external_systems) discovered in a config file."""
    datasources: list[dict] = []
    external: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _DSN_KEYS.search(line):
            kind = _classify_dsn(line)
            key = line.split("=")[0].split(":")[0].strip()
            datasources.append({"dsn_env": key, "kind": kind, "masked": _mask(line)})
        elif _BASEURL_KEY.search(line) and ("http" in line.lower() or "url" in line.lower()):
            key = line.split("=")[0].split(":")[0].strip()
            external.append({"base_url_config_key": key, "masked": _mask(line)})
    return datasources, external


def _parse_dockerfile(text: str) -> dict:
    ports = re.findall(r"^EXPOSE\s+(\d+)", text, re.MULTILINE)
    env_vars = re.findall(r"^ENV\s+([A-Z0-9_]+)", text, re.MULTILINE)
    base = re.search(r"^FROM\s+(\S+)", text, re.MULTILINE)
    return {
        "source": "Dockerfile",
        "image": base.group(1) if base else None,
        "ports": ports,
        "env_vars": sorted(set(env_vars)),
    }


def _parse_compose(text: str) -> list[dict]:
    units: list[dict] = []
    try:
        import yaml
        data = yaml.safe_load(text) or {}
    except Exception:  # noqa: BLE001
        return units
    for name, svc in (data.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        ports = [str(p) for p in (svc.get("ports") or [])]
        env = svc.get("environment") or []
        env_names = (
            list(env.keys()) if isinstance(env, dict)
            else [str(e).split("=")[0] for e in env]
        )
        units.append({
            "name": name,
            "source": "compose",
            "image": svc.get("image"),
            "ports": ports,
            "env_vars": env_names,
            "depends_on": list(svc.get("depends_on") or []),
        })
    return units


_IGNORE_DIRS = {"node_modules", "target", "build", "dist", ".git", ".venv", "__pycache__"}


def _discover_infra_files(root: str) -> list[str]:
    """Walk the project for build/config/infra files (these are NOT in file_inventory,
    which only holds code-language files). Bounded walk, skips heavy dirs."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        # Don't descend too deep — config lives near the top.
        depth = os.path.relpath(dirpath, root).count(os.sep)
        if depth > 4:
            dirnames[:] = []
            continue
        for fn in filenames:
            low = fn.lower()
            if (low in _BUILD_FILES or low in _DEPLOY_FILES or low in _CONFIG_GLOBS
                    or low == "dockerfile" or low.endswith((".env.example", ".env.sample"))):
                found.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return found


async def config_infra_node(state: CodeDocState, *, config: dict) -> dict:
    root = state["project_path"]

    build_deps: dict[str, dict] = {}
    datasources: list[dict] = []
    external_systems: list[dict] = []
    deployment_units: list[dict] = []

    for rel in _discover_infra_files(root):
        base = os.path.basename(rel).lower()
        full = os.path.join(root, rel)

        if base in _BUILD_FILES:
            text = _read(full)
            if base == "package.json":
                build_deps[rel] = _parse_package_json(text)
            elif base == "pom.xml":
                build_deps[rel] = _parse_pom(text)
            elif base.startswith("build.gradle"):
                build_deps[rel] = _parse_gradle(text)
        elif base in _CONFIG_GLOBS or base.endswith((".env.example", ".env.sample")):
            text = _read(full)
            ds, ext = _scan_config_text(text)
            for d in ds:
                d["discovered_from"] = rel
                datasources.append(d)
            for e in ext:
                e["discovered_from"] = rel
                external_systems.append(e)
        elif base == "dockerfile":
            unit = _parse_dockerfile(_read(full))
            unit["name"] = os.path.dirname(rel) or "app"
            deployment_units.append(unit)
        elif base in {"docker-compose.yml", "docker-compose.yaml"}:
            deployment_units.extend(_parse_compose(_read(full)))

    # Dedup datasources by (kind, dsn_env).
    seen = set()
    dedup_ds = []
    for d in datasources:
        key = (d.get("kind"), d.get("dsn_env"))
        if key not in seen:
            seen.add(key)
            dedup_ds.append(d)

    config_infra = {
        "build_deps": build_deps,
        "datasources": dedup_ds,
        "external_systems": external_systems,
        "deployment_units": deployment_units,
    }
    logger.info(
        "config_infra_done",
        build_files=len(build_deps),
        datasources=len(dedup_ds),
        external=len(external_systems),
        deploy_units=len(deployment_units),
    )
    return {"config_infra": config_infra}
