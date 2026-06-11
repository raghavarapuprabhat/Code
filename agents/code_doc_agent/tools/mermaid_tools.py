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


def render_sequence_diagram(flow: dict) -> str:
    """Render a sequence diagram for a single flow."""
    lines = ["sequenceDiagram"]
    actors = []
    for step in flow.get("steps", []):
        # step format: "Actor->Other: action"
        if "->" in step and ":" in step:
            head, _, _ = step.partition(":")
            for actor in head.split("->"):
                a = actor.strip()
                if a and a not in actors:
                    actors.append(a)
                    lines.append(f"    participant {_safe_id(a)} as {a}")
    for step in flow.get("steps", []):
        if "->" in step and ":" in step:
            head, _, msg = step.partition(":")
            a, _, b = head.partition("->")
            lines.append(f"    {_safe_id(a.strip())}->>{_safe_id(b.strip())}: {msg.strip()}")
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
