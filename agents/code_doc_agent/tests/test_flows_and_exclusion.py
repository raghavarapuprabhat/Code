"""Tests for Java flow detection + test-folder exclusion (review fixes).

Covers:
  1. Test-folder exclusion patterns (Java / JS-TS / Python conventions) — config-driven,
     incl. directory pruning and no false positives on production code.
  2. Flow-data enrichment — the tree-graph carries class stereotypes + method HTTP
     mappings, and cross_file extracts the concrete endpoint list, so the LLM flow tracer
     receives the signal it needs for Java (the prior bug: it got bare method names).

Run:  python agents/code_doc_agent/tests/test_flows_and_exclusion.py
"""
from __future__ import annotations

import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../../.."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_CONFIG = os.path.join(_REPO_ROOT, "agents/code_doc_agent/config.yaml")


# --- 1. Test-folder exclusion ----------------------------------------------

def test_test_exclusion_patterns():
    from agents.code_doc_agent.tools.fs_tools import is_ignored

    pats = yaml.safe_load(open(_CONFIG))["code_doc"]["ignore_patterns"]
    excluded = [
        "OrderServiceTest.java", "com/x/OrderServiceTest.java",
        "src/test/java/com/x/FooTest.java", "com/x/PaymentIT.java",
        "src/components/__tests__/Button.test.tsx", "src/components/Button.test.tsx",
        "api/users.spec.ts", "app/test_main.py", "app/main_test.py",
        "foo/tests/baz.js", "foo/testing/util.ts",
    ]
    kept = [
        "src/main/java/com/x/OrderService.java",
        "src/main/java/com/x/OrderController.java",
        "src/services/UserService.java",
        "app/main.py",
        # false-positive guards: 'test' in name but not a test file/segment
        "src/main/TestableConfig.java",
        "src/contest/Foo.java",
    ]
    for p in excluded:
        assert is_ignored(p, pats), f"should be excluded: {p}"
    for p in kept:
        assert not is_ignored(p, pats), f"should be kept: {p}"
    print(f"test exclusion OK: {len(excluded)} excluded, {len(kept)} kept (no false positives)")


def test_test_dir_pruning():
    """`**/<dir>/**` patterns also prune the directory itself during the walk."""
    from agents.code_doc_agent.tools.fs_tools import is_ignored
    pats = yaml.safe_load(open(_CONFIG))["code_doc"]["ignore_patterns"]
    for d in ("src/test", "com/x/test", "src/__tests__", "foo/tests", "foo/testing"):
        assert is_ignored(d, pats), f"dir should be pruned: {d}"
    for d in ("src/main", "com/x/service"):
        assert not is_ignored(d, pats), f"dir should be kept: {d}"
    print("test dir pruning OK")


# --- 2. Java flow-data enrichment ------------------------------------------

_JAVA_CONTROLLER = """
package com.shop;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
public class OrderController {
    private final OrderService orderService;
    @GetMapping("/{id}")
    public Order get(@PathVariable Long id) { return orderService.find(id); }
    @PostMapping
    public Order create(@RequestBody OrderRequest req) { return orderService.place(req); }
}
"""
_JAVA_SERVICE = """
package com.shop;
import org.springframework.stereotype.Service;
@Service
public class OrderService {
    public Order find(Long id) { return null; }
    public Order place(OrderRequest r) { return null; }
}
"""


def _build_asts():
    from agents.code_doc_agent.tools.treesitter_tools import parse_file
    return {
        "src/main/java/com/shop/OrderController.java":
            parse_file(_JAVA_CONTROLLER, "java", "src/main/java/com/shop/OrderController.java").model_dump(),
        "src/main/java/com/shop/OrderService.java":
            parse_file(_JAVA_SERVICE, "java", "src/main/java/com/shop/OrderService.java").model_dump(),
    }


def test_endpoints_extracted_for_flow_prompt():
    from agents.code_doc_agent.nodes.cross_file import _endpoints_block
    eps = _endpoints_block(_build_asts())
    paths = {(e["method"], e["path"]) for e in eps}
    assert ("GET", "/orders/{id}") in paths, eps
    assert ("POST", "/orders") in paths, eps
    post = next(e for e in eps if e["method"] == "POST")
    assert post["request_body"] == "OrderRequest"
    assert post["handler"] == "OrderController.create"
    print("endpoints for flow prompt OK:", sorted(paths))


def test_tree_graph_carries_stereotypes_and_mappings():
    from agents.code_doc_agent.nodes.tree_graph import build_tree_graph
    from agents.code_doc_agent.nodes.cross_file import _compact_tree
    ct = _compact_tree(build_tree_graph(_build_asts()))
    classes = {n["name"]: n for n in ct["nodes"] if n["kind"] == "class"}
    methods = {n["name"]: n for n in ct["nodes"] if n["kind"] == "method"}
    assert "RestController" in classes["OrderController"].get("annotations", [])
    assert "Service" in classes["OrderService"].get("annotations", [])
    assert any("GetMapping" in a for a in methods["get"].get("annotations", []))
    print("tree-graph stereotypes + HTTP mappings OK")


if __name__ == "__main__":
    test_test_exclusion_patterns()
    test_test_dir_pruning()
    test_endpoints_extracted_for_flow_prompt()
    test_tree_graph_carries_stereotypes_and_mappings()
    print("\nALL FLOW + EXCLUSION TESTS PASSED")
