"""LangGraph definition for the SRE Fixer Agent.

Flow:
    context_load -> plan_fix -> apply_patch -> run_tests
        tests passed -> branch_commit -> open_pr -> END
        tests failed -> analyze_failure
            should_retry & attempts<max -> plan_fix (loop)
            otherwise                    -> raise_human -> END
    Any safety / planner / push failure -> raise_human -> END
"""
from __future__ import annotations

import os
from functools import partial
from typing import Any

import yaml
from langgraph.graph import END, StateGraph

from .nodes.analyze_failure import analyze_failure_node
from .nodes.apply_patch import apply_patch_node
from .nodes.branch_commit import branch_commit_node, open_pr_node, raise_human_node
from .nodes.context_load import context_load_node
from .nodes.plan_fix import plan_fix_node
from .nodes.run_tests import run_tests_node
from .state import FixerState

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def build_graph(config: dict[str, Any] | None = None):
    cfg = config or load_config()

    g = StateGraph(FixerState)
    g.add_node("context_load", partial(context_load_node, config=cfg))
    g.add_node("plan_fix", partial(plan_fix_node, config=cfg))
    g.add_node("apply_patch", partial(apply_patch_node, config=cfg))
    g.add_node("run_tests", partial(run_tests_node, config=cfg))
    g.add_node("analyze_failure", partial(analyze_failure_node, config=cfg))
    g.add_node("branch_commit", partial(branch_commit_node, config=cfg))
    g.add_node("open_pr", partial(open_pr_node, config=cfg))
    g.add_node("raise_human", partial(raise_human_node, config=cfg))

    g.set_entry_point("context_load")

    def post_context(state: FixerState) -> str:
        return "raise_human" if state.get("status") in {"error", "raised_human"} else "plan_fix"

    g.add_conditional_edges("context_load", post_context, {
        "plan_fix": "plan_fix",
        "raise_human": "raise_human",
    })

    def post_plan(state: FixerState) -> str:
        return "raise_human" if state.get("status") == "raised_human" else "apply_patch"

    g.add_conditional_edges("plan_fix", post_plan, {
        "apply_patch": "apply_patch",
        "raise_human": "raise_human",
    })

    def post_apply(state: FixerState) -> str:
        return "raise_human" if state.get("status") == "raised_human" else "run_tests"

    g.add_conditional_edges("apply_patch", post_apply, {
        "run_tests": "run_tests",
        "raise_human": "raise_human",
    })

    def post_tests(state: FixerState) -> str:
        s = state.get("status")
        if s == "tests_passed":
            return "branch_commit"
        if s == "raised_human":
            return "raise_human"
        return "analyze_failure"

    g.add_conditional_edges("run_tests", post_tests, {
        "branch_commit": "branch_commit",
        "analyze_failure": "analyze_failure",
        "raise_human": "raise_human",
    })

    def post_analyze(state: FixerState) -> str:
        return "plan_fix" if state.get("status") == "planning" else "raise_human"

    g.add_conditional_edges("analyze_failure", post_analyze, {
        "plan_fix": "plan_fix",
        "raise_human": "raise_human",
    })

    def post_branch(state: FixerState) -> str:
        return "open_pr" if state.get("status") == "branch_created" else "raise_human"

    g.add_conditional_edges("branch_commit", post_branch, {
        "open_pr": "open_pr",
        "raise_human": "raise_human",
    })

    g.add_edge("open_pr", END)
    g.add_edge("raise_human", END)

    return g.compile()


graph = build_graph()


async def run_fix(
    *,
    project_id: str,
    handoff: dict,
    azure_repo: dict | None = None,
    repo_path: str | None = None,
) -> dict:
    """Single fixer invocation.

    Args:
        project_id: from the Code Doc agent — used to resolve repo_path from DB.
        handoff: payload produced by the SRE Agent (issue + verdict + likely_files).
        azure_repo: {"project": ..., "repository_id": ..., "target_branch": "refs/heads/main"}.
        repo_path: optional explicit override for the working tree path.
    """
    cfg = load_config()
    g = build_graph(cfg)
    initial: FixerState = {
        "project_id": project_id,
        "handoff": handoff,
        "azure_repo": azure_repo or {},
        "audit_trail": [],
        "attempt": 0,
    }
    if repo_path:
        initial["repo_path"] = repo_path
    return dict(await g.ainvoke(initial))
