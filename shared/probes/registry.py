"""Environment registry for runtime probes (architecture §9.7A, §16).

Discovered code config gives a probe its *shape* (paths, schemas, env-var names); the
live coordinates (actual host, credentials) come from an `environments.yaml` registry.
Targets carry **env-var names**, never secret values — values are read from the process
env at call time and never enter the LLM context, logs, or Evidence rows.

environments.yaml:

    environments:
      dev:
        classification: dev
        http:   { checkout-api: { base_url_env: CHECKOUT_API_DEV_URL } }
        db:     { orders-db:    { dsn_env: ORDERS_DB_DEV_URL } }
      prod:
        classification: prod
        http:   { checkout-api: { base_url_env: CHECKOUT_API_PROD_URL } }
        db:     { orders-db:    { dsn_env: ORDERS_DB_PROD_URL } }
"""
from __future__ import annotations

import os
from typing import Any

import yaml

_SEARCH_PATHS = [
    os.getenv("PROBE_ENVIRONMENTS", ""),
    os.path.join(os.path.dirname(__file__), "..", "..", "agents", "sre_agent", "environments.yaml"),
    os.path.join(os.path.dirname(__file__), "..", "..", "infra", "environments.yaml"),
]


def load_environments(path: str | None = None) -> dict[str, Any]:
    candidates = [path] if path else _SEARCH_PATHS
    for p in candidates:
        if p and os.path.isfile(p):
            try:
                with open(p) as fh:
                    return (yaml.safe_load(fh) or {}).get("environments", {}) or {}
            except Exception:  # noqa: BLE001
                continue
    return {}


def classify_env(environment: str | None) -> str:
    """dev | test | prod — prod requires an approval gate before the first probe."""
    if not environment:
        return "dev"
    e = environment.lower()
    if e.startswith("prod"):
        return "prod"
    if e in {"test", "qa", "staging", "stage", "uat"}:
        return "test"
    return "dev"


def resolve_target(kind: str, name: str, environment: str, *, path: str | None = None) -> dict | None:
    """Resolve a (kind, name, environment) into a ProbeTarget dict, or None if unknown."""
    envs = load_environments(path)
    env_block = envs.get(environment) or envs.get(classify_env(environment)) or {}
    entry = (env_block.get(kind) or {}).get(name)
    if not entry:
        return None
    ref_key = "base_url_env" if kind == "http" else "dsn_env"
    ref = entry.get(ref_key)
    if not ref:
        return None
    return {
        "kind": kind,
        "name": name,
        "environment": environment,
        "base_url_or_dsn_ref": ref,            # env-var NAME, never the value
        "discovered_from": f"environments.yaml:{environment}.{kind}.{name}",
        "approved": classify_env(environment) != "prod",
    }


def list_targets(kind: str, *, path: str | None = None) -> list[dict]:
    """All configured targets of a kind across environments (for discovery hints)."""
    out: list[dict] = []
    for env_name, block in (load_environments(path) or {}).items():
        for name in (block.get(kind) or {}):
            out.append({"kind": kind, "name": name, "environment": env_name})
    return out


def get_secret(env_var_name: str) -> str | None:
    """Read a secret by env-var name at call time. Never log the return value."""
    return os.getenv(env_var_name)
