"""Mermaid diagram builders."""
from __future__ import annotations

from typing import Iterable


def render_module_diagram(modules: list[dict]) -> str:
    """Top-level component diagram — modules as boxes."""
    lines = ["flowchart LR"]
    for i, m in enumerate(modules):
        node_id = f"M{i}"
        lines.append(f'    {node_id}["{m["name"]}"]')
    return "\n".join(lines)


def render_call_graph(call_graph: dict) -> str:
    """Render a directed call graph as Mermaid.

    Expects: {"edges": [["from_module/file", "to_module/file"], ...]}
    """
    lines = ["flowchart LR"]
    seen: set[str] = set()
    for edge in call_graph.get("edges", []):
        a, b = edge
        for n in (a, b):
            if n not in seen:
                lines.append(f'    {_safe_id(n)}["{n}"]')
                seen.add(n)
        lines.append(f"    {_safe_id(a)} --> {_safe_id(b)}")
    return "\n".join(lines)


def parse_flow_step(step: str) -> tuple[str, str, str, bool] | None:
    """Parse a flow step "Source->Target: message" (or "-->" for a return).

    Returns (source, target, message, is_return) or None if the step is not an
    arrow step. Splits on "-->" before "->" so a return arrow yields a clean
    target name (not "Source-"); this is the fix for the malformed-actor bug.
    """
    if ":" not in step:
        return None
    head, _, msg = step.partition(":")
    if "-->" in head:
        a, _, b = head.partition("-->")
        is_return = True
    elif "->" in head:
        a, _, b = head.partition("->")
        is_return = False
    else:
        return None
    a, b = a.strip(), b.strip()
    if not a or not b:
        return None
    return a, b, msg.strip(), is_return


def render_sequence_diagram(flow: dict) -> str:
    """Render a sequence diagram for a single flow."""
    lines = ["sequenceDiagram"]
    actors: list[str] = []
    parsed = [(s, parse_flow_step(s)) for s in flow.get("steps", [])]
    for _step, p in parsed:
        if p:
            a, b, _msg, _ret = p
            for actor in (a, b):
                if actor not in actors:
                    actors.append(actor)
                    lines.append(f"    participant {_safe_id(actor)} as {actor}")
    for step, p in parsed:
        if p:
            a, b, msg, is_return = p
            arrow = "-->>" if is_return else "->>"
            lines.append(f"    {_safe_id(a)}{arrow}{_safe_id(b)}: {msg}")
        else:
            lines.append(f"    Note over {_safe_id(actors[0]) if actors else 'System'}: {step}")
    return "\n".join(lines)


def render_er_diagram(entities: Iterable[dict]) -> str:
    lines = ["erDiagram"]
    for ent in entities:
        name = ent["name"]
        fields = ent.get("fields", [])
        lines.append(f"    {name} {{")
        for f in fields:
            lines.append(f"        {f.get('type', 'string')} {f['name']}")
        lines.append("    }")
    for ent in entities:
        for rel in ent.get("relations", []):
            lines.append(
                f"    {ent['name']} {rel.get('cardinality', '||--o{')} {rel['target']} : {rel.get('label', 'has')}"
            )
    return "\n".join(lines)


def _safe_id(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s) or "n"
