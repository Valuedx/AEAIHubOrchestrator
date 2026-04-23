"""SMART-04 — proactive authoring lints for the copilot.

Cheap, structure-level checks that run after every draft mutation.
They sit one level *above* the schema validator (which only checks
config fields against ``node_registry.json``) and catch the class
of bugs that don't fail schema validation but still mean "this
workflow won't work the way you hope":

  * ``no_trigger`` — graph has nodes but no way to start.
  * ``disconnected_node`` — node is in the graph but not reachable
    from any trigger through directed edges.
  * ``orphan_edge`` — edge references a node_id that isn't in the
    graph (can happen after an external graph edit or a race).
  * ``missing_credential`` — LLM-family node has a provider set
    but the resolver can't find a key for it (env or per-tenant
    secret).

Each returns a ``Lint`` result. The runner-tool layer augments
``check_draft``'s output with a list of these; the agent surfaces
them in its narration so the user sees them before they run the
draft.

**Design rules**
  * All rules are O(nodes + edges). No LLM calls. No network.
  * DB-dependent rules (``missing_credential``) receive
    ``tenant_id`` + ``db`` so they can consult the credentials
    resolver. Rules that don't need DB take a ``graph_json`` dict
    only — keeps unit tests cheap.
  * Rules are small pure functions; ``run_lints`` composes them.
  * Each rule returns the *structured* lint — never a string.
    The UI wants per-severity colours, a clickable fix hint, and
    a stable ``code`` field for deduping across turns.

**Opt-out**
  * The runner-tool layer reads ``smart_04_lints_enabled`` from
    the tenant policy resolver. When off, ``run_lints`` is not
    called at all. This module is agnostic — it always runs when
    invoked.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


Severity = str  # "error" | "warn" — using str instead of Literal for easy JSON.


@dataclass
class Lint:
    """One lint finding. Stable shape so the frontend can render a
    consistent per-severity card."""

    code: str
    """Stable identifier (``no_trigger``, ``disconnected_node``, ...).
    The UI dedupes by ``(code, node_id)`` so a node that fires the
    same rule on two consecutive turns doesn't create two list
    items."""

    severity: Severity
    """``error`` = the workflow will fail at runtime (user must fix).
    ``warn`` = may still run but probably not what the user intended."""

    message: str
    """Human-readable. Kept short; the ``fix_hint`` carries the
    specifics."""

    fix_hint: str | None = None
    """Optional "here's how to resolve this" line. The chat-pane
    could turn this into a "Fix with copilot" button later."""

    node_id: str | None = None
    """Which node the lint applies to. ``None`` for graph-wide
    findings like ``no_trigger``."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Graph-structure lints — no DB, O(nodes + edges)
# ---------------------------------------------------------------------------


def _nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return list(graph.get("nodes") or [])


def _edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return list(graph.get("edges") or [])


def _is_trigger(node: dict[str, Any]) -> bool:
    """A node is a trigger iff its registry category is ``trigger``.
    Matches the node_registry.json convention used by the DAG
    runner and the scheduler."""
    data = node.get("data") or {}
    return (data.get("nodeCategory") or "") == "trigger"


def lint_no_trigger(graph: dict[str, Any]) -> list[Lint]:
    nodes = _nodes(graph)
    if not nodes:
        # Empty graph isn't broken — user may be about to add nodes.
        # Only fire when there are nodes but none is a trigger.
        return []
    if any(_is_trigger(n) for n in nodes):
        return []
    return [Lint(
        code="no_trigger",
        severity="error",
        message="Workflow has no trigger node — there's no way to start it.",
        fix_hint=(
            "Add a `webhook_trigger` (for API calls) or "
            "`schedule_trigger` (for cron) and connect it upstream "
            "of your first action."
        ),
        node_id=None,
    )]


def lint_orphan_edges(graph: dict[str, Any]) -> list[Lint]:
    node_ids = {n.get("id") for n in _nodes(graph) if n.get("id")}
    out: list[Lint] = []
    for edge in _edges(graph):
        source = edge.get("source")
        target = edge.get("target")
        edge_id = edge.get("id") or f"({source}→{target})"
        if source not in node_ids or target not in node_ids:
            missing = source if source not in node_ids else target
            out.append(Lint(
                code="orphan_edge",
                severity="error",
                message=(
                    f"Edge `{edge_id}` references node `{missing}` "
                    "which isn't in the graph."
                ),
                fix_hint="Call `disconnect_edge` to drop the stale edge, then reconnect.",
                node_id=None,
            ))
    return out


def lint_disconnected_nodes(graph: dict[str, Any]) -> list[Lint]:
    """A node is disconnected if there's no path to it from any
    trigger via directed edges. Isolated triggers themselves pass
    (they're the entry point; they'll fire on their own schedule /
    webhook regardless of incoming edges)."""
    nodes = _nodes(graph)
    node_ids = {n.get("id") for n in nodes if n.get("id")}
    if not node_ids:
        return []

    triggers = [n.get("id") for n in nodes if _is_trigger(n) and n.get("id")]
    if not triggers:
        # Handled by lint_no_trigger — no need to double-surface.
        return []

    # Build adjacency (source → list[target]) ignoring orphan edges
    # (those are reported separately).
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids if nid}
    for edge in _edges(graph):
        src, tgt = edge.get("source"), edge.get("target")
        if src in node_ids and tgt in node_ids:
            adj[src].append(tgt)

    # BFS from every trigger.
    reachable: set[str] = set()
    for trig in triggers:
        if trig is None:
            continue
        stack = [trig]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(adj.get(cur, []))

    out: list[Lint] = []
    for node in nodes:
        nid = node.get("id")
        if not nid or nid in reachable:
            continue
        label = (node.get("data") or {}).get("label", "node")
        out.append(Lint(
            code="disconnected_node",
            severity="warn",
            message=(
                f"Node `{nid}` (“{label}”) isn't reachable from any "
                "trigger — it won't run."
            ),
            fix_hint=(
                "Either `connect_nodes` it into the flow or "
                "`delete_node` if it's no longer needed."
            ),
            node_id=nid,
        ))
    return out


# ---------------------------------------------------------------------------
# Credential lint — needs db + tenant_id via the ADMIN-03 resolver
# ---------------------------------------------------------------------------


# Map node label → which credential resolver to call. Adding a new
# LLM-family node type here is a one-line change. Deliberately keyed
# on the human label (which is what ``data.label`` stores) rather
# than the registry type id so it matches what the graph_json
# contains without a second lookup.
_LLM_LABELS = frozenset({
    "LLM Agent",
    "ReAct Agent",
    "LLM Router",
    "Reflection",
    "Intent Classifier",
})


def lint_missing_credentials(
    graph: dict[str, Any],
    *,
    tenant_id: str,
    db: Session | None,
) -> list[Lint]:
    """For each LLM-family node, look up the provider config and
    verify the ADMIN-03 resolver can produce a key. Vertex is
    deliberately excluded because it uses ADC (no per-request key);
    its credential surface is project/location routing, which has
    its own resolver path via VERTEX-02.

    A missing key is surfaced as ``error`` because the node will
    raise ``ValueError`` the first time it runs.
    """
    from app.engine.llm_credentials_resolver import get_credentials_status

    out: list[Lint] = []
    nodes_needing_check = [
        n for n in _nodes(graph)
        if (n.get("data") or {}).get("label") in _LLM_LABELS
    ]
    if not nodes_needing_check:
        return out

    # get_credentials_status is one DB round-trip per tenant; we call
    # it once and reuse. When ``db`` is None (unit tests) we skip
    # the check and return no lints — not having db context in prod
    # would be a bug, but a stale lint isn't worth blowing up the
    # agent's turn either.
    if db is None:
        return out
    try:
        providers = get_credentials_status(tenant_id)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "SMART-04 lint_missing_credentials: resolver failed for tenant=%r: %s",
            tenant_id, exc,
        )
        return out

    for node in nodes_needing_check:
        nid = node.get("id")
        data = node.get("data") or {}
        config = data.get("config") or {}
        provider = config.get("provider")
        if not provider:
            # The schema validator will already have flagged this;
            # don't double-surface.
            continue
        if provider == "vertex":
            # ADC-backed — no per-tenant key to check.
            continue

        prov_info = providers.get(provider)
        if prov_info is None:
            out.append(Lint(
                code="missing_credential",
                severity="error",
                message=(
                    f"Node `{nid}` uses provider `{provider}` which "
                    "isn't wired up for this tenant."
                ),
                fix_hint=(
                    "Open the LLM Credentials dialog (toolbar Key "
                    "icon) and add a key, or set the "
                    f"ORCHESTRATOR_{provider.upper()}_API_KEY env "
                    "default."
                ),
                node_id=nid,
            ))
            continue

        source = prov_info.get("source")
        if source == "not_configured":
            out.append(Lint(
                code="missing_credential",
                severity="error",
                message=(
                    f"Node `{nid}` uses provider `{provider}` but no "
                    "API key is configured (neither a tenant secret "
                    "nor an env default)."
                ),
                fix_hint=(
                    "Add the tenant key via the LLM Credentials "
                    "dialog, or populate "
                    f"ORCHESTRATOR_{provider.upper()}_API_KEY."
                ),
                node_id=nid,
            ))
    return out


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CYCLIC-01.c — loopback edge lints
# ---------------------------------------------------------------------------


def lint_loopback_no_exit(graph: dict[str, Any]) -> list[Lint]:
    """Error — a loopback cycle with no forward exit path can only
    terminate via the iteration cap. Almost always a bug. Same
    rule the validator enforces at save time, but surfaced through
    ``check_draft`` so the copilot flags it mid-authoring before
    the operator hits Promote."""
    from app.engine.cyclic_analysis import (
        cycle_body,
        has_forward_exit,
        is_forward_ancestor,
        loopback_edges,
    )

    out: list[Lint] = []
    for edge in loopback_edges(graph):
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        # Skip malformed cycles — the validator catches those
        # separately; piling two errors onto the same edge is noisy.
        if not is_forward_ancestor(target, source, graph):
            continue
        body = cycle_body(target, source, graph)
        if body and not has_forward_exit(body, graph):
            out.append(Lint(
                code="loopback_no_exit",
                severity="error",
                message=(
                    f"Loopback {source}→{target}: the cycle has no forward "
                    "exit path."
                ),
                fix_hint=(
                    "Add a Condition whose false branch exits the cycle so "
                    "the workflow can terminate on something other than the "
                    "iteration cap."
                ),
                node_id=target,
            ))
    return out


def lint_loopback_no_cap(graph: dict[str, Any]) -> list[Lint]:
    """Warn — loopback edge relies on the default iteration cap
    (``maxIterations`` missing from the edge entirely). The default
    is sensible (10) but silent; flagging it nudges authors to
    set an explicit number so someone reading the graph a month
    later doesn't have to look up the default."""
    from app.engine.cyclic_analysis import loopback_edges

    out: list[Lint] = []
    for edge in loopback_edges(graph):
        if edge.get("maxIterations") is None:
            source = str(edge.get("source"))
            target = str(edge.get("target"))
            out.append(Lint(
                code="loopback_no_cap",
                severity="warn",
                message=(
                    f"Loopback {source}→{target}: using the default "
                    "maxIterations of 10."
                ),
                fix_hint=(
                    "Set an explicit maxIterations on the edge so the cap is "
                    "visible in graph_json. Valid range: 1 to 100."
                ),
                node_id=target,
            ))
    return out


def lint_loopback_nested_deep(graph: dict[str, Any]) -> list[Lint]:
    """Warn — three or more distinct cycles in one graph. Cycles
    interact in subtle ways (cap counters are per-edge, body
    clearing is per-edge, nested cycle body inclusion can be
    surprising). Authors should split deeply nested cyclic
    authoring into sub-workflows that each own their own cycle."""
    from app.engine.cyclic_analysis import deduped_bodies

    bodies = deduped_bodies(graph)
    if len(bodies) < 3:
        return []
    return [Lint(
        code="loopback_nested_deep",
        severity="warn",
        message=(
            f"Graph has {len(bodies)} distinct cycles — deeply nested "
            "cyclic logic tends to interact in surprising ways."
        ),
        fix_hint=(
            "Consider extracting one or more cycles into a Sub-Workflow. "
            "Each sub-workflow owns its own iteration counters and "
            "cycle-body clearing, which is easier to reason about."
        ),
        node_id=None,
    )]


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def run_lints(
    graph: dict[str, Any],
    *,
    tenant_id: str,
    db: Session | None,
) -> list[Lint]:
    """Run all lint rules and return a combined, de-duplicated list.

    Runtime: O(nodes + edges) for the structure lints + one DB
    round-trip for ``missing_credential`` (skipped when ``db`` is
    None). Callers should check the tenant's ``smart_04_lints_enabled``
    flag BEFORE invoking this — the composer is feature-flag-agnostic.
    """
    lints: list[Lint] = []
    lints.extend(lint_no_trigger(graph))
    lints.extend(lint_orphan_edges(graph))
    lints.extend(lint_disconnected_nodes(graph))
    lints.extend(lint_missing_credentials(graph, tenant_id=tenant_id, db=db))
    # CYCLIC-01.c — cycle-aware rules. Each checks for loopback
    # edges first and no-ops when there aren't any, so the
    # zero-loopback hot path stays near-zero cost.
    lints.extend(lint_loopback_no_exit(graph))
    lints.extend(lint_loopback_no_cap(graph))
    lints.extend(lint_loopback_nested_deep(graph))
    return lints
