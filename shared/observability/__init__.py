"""Observability adapters shared across agents (architecture §9.17.1)."""
from .adapters import (
    build_log_adapter,
    build_metrics_adapter,
    deployments_enabled,
    get_deployments,
)
from .parser import parse_logs

__all__ = [
    "build_log_adapter",
    "build_metrics_adapter",
    "deployments_enabled",
    "get_deployments",
    "parse_logs",
]
