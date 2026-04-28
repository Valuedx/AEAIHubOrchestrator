"""CYCLIC-01.c — pure graph-analysis helpers for loopback edges.

Shared by ``config_validator`` (save-time hard errors) and
``app.copilot.lints`` (post-mutation copilot warnings). Split into
its own module to avoid importing ``dag_runner`` (which pulls in
DB + engine + LLM client dependencies) from the validator layer.

Every helper here is a pure function over ``graph_json`` — no DB,
no engine state, no lazy registry load. Matches the rest of the
validator's "cheap, deterministic, under 100 lines" shape.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


# Mirrors the constants in ``app.engine.dag_runner``. Duplicated
# rather than imported because ``config_validator`` can't take a
# runtime dependency on the engine without creating an import
# cycle (engine → validator at save time; validator → engine
# would close the loop). Changing either value requires updating
# both + the frontend constant in ``frontend/src/types/edges.ts``.
LOOPBACK_DEFAULT_MAX_ITERATIONS = 10
LOOPBACK_HARD_CAP = 100


def _edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return list(graph.get("edges") or [])


def _nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return list(graph.get("nodes") or [])


def _executable_node_ids(graph: dict[str, Any]) -> set[str]:
    """Mirrors ``parse_graph``'s filter — only ``agenticNode``
    entries (and legacy nodes without a ``type``) are executable.
    Sticky notes don't participate in cycles."""
    return {
        str(n["id"])
        for n in _nodes(graph)
        if n.get("type", "agenticNode") == "agenticNode" and "id" in n
    }


def loopback_edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """All edges with ``type == "loopback"``. Edges referencing
    filtered-out nodes (sticky targets, etc.) are excluded so
    downstream analysis doesn't trip on dangling references."""
    executable = _executable_node_ids(graph)
    return [
        e for e in _edges(graph)
        if e.get("type") == "loopback"
        and e.get("source") in executable
        and e.get("target") in executable
    ]


def forward_edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """All non-loopback edges between executable nodes. This is the
    forward subgraph — the graph the engine actually walks."""
    executable = _executable_node_ids(graph)
    return [
        e for e in _edges(graph)
        if e.get("type") != "loopback"
        and e.get("source") in executable
        and e.get("target") in executable
    ]


def forward_adjacency(graph: dict[str, Any]) -> dict[str, list[str]]:
    """source_id → [target_id, ...] over the forward subgraph only.
    Callers use this for ancestor / descendant walks."""
    adj: dict[str, list[str]] = defaultdict(list)
    for e in forward_edges(graph):
        adj[str(e["source"])].append(str(e["target"]))
    return dict(adj)


def reverse_adjacency(graph: dict[str, Any]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for e in forward_edges(graph):
        adj[str(e["target"])].append(str(e["source"]))
    return dict(adj)


def reachable_from(
    source: str, adj: dict[str, list[str]],
) -> set[str]:
    """BFS reachability over the supplied adjacency map. Used by
    both ``is_ancestor`` (forward adj) and the cycle-body
    computation (both directions)."""
    seen: set[str] = set()
    stack = [source]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(adj.get(nid, []))
    return seen


def is_forward_ancestor(
    candidate: str,
    descendant: str,
    graph: dict[str, Any],
) -> bool:
    """True iff ``candidate`` can reach ``descendant`` via forward
    edges. Used by the validator to reject edges whose "target" is
    not actually upstream of "source" — those aren't cycles, they're
    forward skips that accidentally tagged themselves as loopbacks.
    """
    if candidate == descendant:
        return True
    adj = forward_adjacency(graph)
    return descendant in reachable_from(candidate, adj)


def cycle_body(
    target: str,
    source: str,
    graph: dict[str, Any],
) -> set[str]:
    """Every node that sits on at least one forward path from
    ``target`` to ``source``, inclusive of both endpoints. Mirrors
    ``dag_runner._compute_cycle_body`` semantically — kept in sync
    by unit tests on both sides that use the same fixture shapes.
    """
    fwd = forward_adjacency(graph)
    rev = reverse_adjacency(graph)
    return reachable_from(target, fwd) & reachable_from(source, rev)


def has_forward_exit(
    body: set[str],
    graph: dict[str, Any],
) -> bool:
    """Is there at least one forward edge whose source is in the
    cycle body AND whose target is OUTSIDE the cycle body? If not,
    the loopback's iteration cap is the only thing stopping
    execution — almost always a bug."""
    for e in forward_edges(graph):
        if str(e["source"]) in body and str(e["target"]) not in body:
            return True
    return False


def loopback_max_iterations(edge: dict[str, Any]) -> int | None:
    """Extract the ``maxIterations`` attribute off an edge, or
    ``None`` if absent / non-integer. Separate from clamping
    because the validator wants to flag the raw value (so it can
    say "you wrote 150, max is 100") while the runtime wants to
    clamp silently."""
    raw = edge.get("maxIterations")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def count_distinct_cycles(graph: dict[str, Any]) -> int:
    """How many distinct cycle bodies does the graph contain?
    Used by the ``loopback_nested_deep`` lint. Two loopback edges
    whose bodies share at least one node are merged (they're the
    same logical cycle from the user's mental model).
    """
    bodies: list[set[str]] = []
    for e in loopback_edges(graph):
        body = cycle_body(str(e["target"]), str(e["source"]), graph)
        if not body:
            continue
        merged = False
        for existing in bodies:
            if body & existing:
                existing |= body
                merged = True
                break
        if not merged:
            bodies.append(body)
    return len(bodies)


def deduped_bodies(graph: dict[str, Any]) -> list[set[str]]:
    """Same merge-on-overlap as ``count_distinct_cycles`` but returns
    the bodies themselves. Lets lint rules report which node is the
    shared point of nested cycles."""
    bodies: list[set[str]] = []
    for e in loopback_edges(graph):
        body = cycle_body(str(e["target"]), str(e["source"]), graph)
        if not body:
            continue
        merged = False
        for existing in bodies:
            if body & existing:
                existing |= body
                merged = True
                break
        if not merged:
            bodies.append(body)
    return bodies


__all__ = [
    "LOOPBACK_DEFAULT_MAX_ITERATIONS",
    "LOOPBACK_HARD_CAP",
    "count_distinct_cycles",
    "cycle_body",
    "deduped_bodies",
    "forward_adjacency",
    "forward_edges",
    "has_forward_exit",
    "is_forward_ancestor",
    "loopback_edges",
    "loopback_max_iterations",
    "reachable_from",
    "reverse_adjacency",
]
