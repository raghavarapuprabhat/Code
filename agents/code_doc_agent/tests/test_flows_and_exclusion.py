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
    private final OrderRepository orderRepository;
    public Order find(Long id) { return null; }
    public Order place(OrderRequest r) { return null; }
}
"""
# Wire OrderController -> OrderService via a field so the deterministic call graph /
# baseline flow can trace the layer chain.
_JAVA_CONTROLLER_WIRED = _JAVA_CONTROLLER
_JAVA_REPOSITORY = """
package com.shop;
import org.springframework.stereotype.Repository;
@Repository
public class OrderRepository {
    public Order save(Order o) { return o; }
}
"""
# Entity with a @MappedSuperclass parent chain (Order -> BaseEntity) + a relation.
_JAVA_BASE_ENTITY = """
package com.shop;
import jakarta.persistence.*;
@MappedSuperclass
public class BaseEntity {
    @Id @GeneratedValue private Integer id;
}
"""
_JAVA_ORDER_ENTITY = """
package com.shop;
import jakarta.persistence.*;
import java.util.List;
@Entity
@Table(name = "orders")
public class Order extends BaseEntity {
    @Column private String status;
    @OneToMany private List<OrderItem> items;
}
"""


def _build_asts():
    from agents.code_doc_agent.tools.treesitter_tools import parse_file
    files = {
        "src/main/java/com/shop/OrderController.java": _JAVA_CONTROLLER,
        "src/main/java/com/shop/OrderService.java": _JAVA_SERVICE,
        "src/main/java/com/shop/OrderRepository.java": _JAVA_REPOSITORY,
        "src/main/java/com/shop/BaseEntity.java": _JAVA_BASE_ENTITY,
        "src/main/java/com/shop/Order.java": _JAVA_ORDER_ENTITY,
    }
    return {p: parse_file(src, "java", p).model_dump() for p, src in files.items()}


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


# --- 3. Deterministic seeds (data model / call graph / flows) --------------

def test_entities_flatten_mapped_superclass_and_relations():
    from agents.code_doc_agent.nodes.cross_file import _entities_from_asts
    ents = {e["name"]: e for e in _entities_from_asts(_build_asts())}
    assert "Order" in ents, ents
    order = ents["Order"]
    field_names = {f["name"] for f in order["fields"]}
    # inherited PK from @MappedSuperclass BaseEntity + own column
    assert "id" in field_names, order
    assert "status" in field_names, order
    # @OneToMany list field becomes a relation, not a scalar field
    assert "items" not in field_names, order
    rel_targets = {r["target"] for r in order["relations"]}
    assert "OrderItem" in rel_targets, order
    print("entities flatten @MappedSuperclass + relations OK:", sorted(field_names), sorted(rel_targets))


def test_call_graph_has_layer_edges():
    from agents.code_doc_agent.nodes.cross_file import _call_graph_from_tree, _endpoints_block
    asts = _build_asts()
    cg = _call_graph_from_tree(asts, _endpoints_block(asts))
    edges = {tuple(e) for e in cg["edges"]}
    assert ("OrderController", "OrderService") in edges, edges
    assert ("OrderService", "OrderRepository") in edges, edges
    assert cg["entry_points"], cg
    print("deterministic call graph layer edges OK:", sorted(edges))


def test_baseline_flows_cover_every_endpoint():
    from agents.code_doc_agent.nodes.cross_file import _baseline_flows, _endpoints_block
    from agents.code_doc_agent.tools.mermaid_tools import parse_flow_step
    asts = _build_asts()
    eps = _endpoints_block(asts)
    flows = _baseline_flows(eps, asts)
    assert len(flows) == len(eps), (len(flows), len(eps))
    # every flow must have at least one parseable Source->Target step
    for f in flows:
        assert any(parse_flow_step(s) for s in f["steps"]), f
    # the POST /orders flow should trace controller -> service -> repository
    post = next(f for f in flows if f["name"].startswith("POST"))
    chain = " ".join(post["steps"])
    assert "OrderController->OrderService" in chain, post
    assert "OrderService->OrderRepository" in chain, post
    print("baseline flows cover every endpoint + trace layers OK")


def test_sequence_return_arrow_no_malformed_actor():
    """Regression: a `-->` return step must not produce a trailing-dash actor."""
    from agents.code_doc_agent.tools.mermaid_tools import render_sequence_diagram, parse_flow_step
    p = parse_flow_step("OrderController-->Client: 201 Created")
    assert p == ("OrderController", "Client", "201 Created", True), p
    out = render_sequence_diagram({"steps": ["OrderController-->Client: 201 Created"]})
    assert "participant OrderController as OrderController" in out, out
    assert "OrderController-" not in out.replace("OrderController->", "").replace("OrderController--", ""), out
    assert "-->>" in out, out  # rendered as a return arrow
    print("sequence return-arrow bug fixed OK")


if __name__ == "__main__":
    test_test_exclusion_patterns()
    test_test_dir_pruning()
    test_endpoints_extracted_for_flow_prompt()
    test_tree_graph_carries_stereotypes_and_mappings()
    test_entities_flatten_mapped_superclass_and_relations()
    test_call_graph_has_layer_edges()
    test_baseline_flows_cover_every_endpoint()
    test_sequence_return_arrow_no_malformed_actor()
    print("\nALL FLOW + EXCLUSION TESTS PASSED")
