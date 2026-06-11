"""SRE Agent tools.

Static (read-only) investigation tools wired into the agentic loop (§9.7). Live
runtime probes (http_probe/db_query), architecture-model tools, and observability
adapters are added in later v0.4/v0.6 phases.
"""
from .registry import available_tools, tool_catalog
from .stacktrace import parse_stack_trace

__all__ = ["available_tools", "tool_catalog", "parse_stack_trace"]
