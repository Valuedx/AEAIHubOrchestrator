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
    # COPILOT-V2 — prompt-cache + react-role consistency.
    lints.extend(lint_prompt_cache_breakage(graph))
    lints.extend(lint_react_role_inconsistency(graph))
    return lints


# ---------------------------------------------------------------------------
# COPILOT-V2 — prompt-cache + ReAct role consistency lints
# ---------------------------------------------------------------------------


# LLM-family node labels carry a `systemPrompt` that goes through Vertex /
# Anthropic prefix caching. Keep in sync with `_LLM_LABELS` above (which is
# scoped to credential checks). We deliberately reuse the same set so a
# new LLM-family node type only needs to be added in one place.
_PROMPT_CACHEABLE_LABELS = _LLM_LABELS

# A prompt is "long enough that prefix caching matters". Below this
# threshold the cache miss is a non-event (~$0); above it, breaking the
# cache costs the user real money. 800 chars ≈ 200 tokens — Anthropic's
# minimum cache breakpoint at the time of writing.
_PROMPT_CACHE_MIN_LENGTH = 800

# When the FIRST Jinja interpolation appears, what fraction of the prompt
# qualifies as "early"? An early interpolation breaks caching for the
# entire prompt — the cache is prefix-only, so any byte change before the
# breakpoint invalidates the cache. The V8/V9/V10 pattern of static rules
# upfront + dynamic per-turn block at the tail keeps the prefix stable.
_PROMPT_CACHE_EARLY_FRACTION = 0.5


def lint_prompt_cache_breakage(graph: dict[str, Any]) -> list[Lint]:
    """Warn when an LLM-family node's ``systemPrompt`` is long enough
    that prefix caching matters AND a Jinja interpolation appears in
    the cacheable prefix region.

    Why this matters: Vertex AI and Anthropic prompt caches require a
    byte-stable prefix. A `{{ trigger.user_role }}` near the top of a
    long system prompt breaks the cache on every turn — for a 5k-token
    prompt that's a real cost. The V8/V9/V10 pattern (memory:
    feedback_agent_design_principles.md) is to put rules + AE
    architecture context in a STATIC block and isolate per-turn
    variables in a single bounded "current turn context" suffix. This
    lint catches the inverse — a long prompt with interpolations
    sprinkled throughout instead of pinned to the tail.

    The warn is graded by where the FIRST interpolation appears: if
    it's in the first 50% of the prompt, the prefix cache is
    near-useless. If it's only in the last 50%, this lint stays quiet
    (the user has already structured the prompt correctly, even if
    they didn't explicitly mark the boundary).
    """
    out: list[Lint] = []
    for node in _nodes(graph):
        data = node.get("data") or {}
        label = data.get("label") or ""
        if label not in _PROMPT_CACHEABLE_LABELS:
            continue
        config = data.get("config") or {}
        prompt = config.get("systemPrompt") or ""
        if not isinstance(prompt, str) or len(prompt) < _PROMPT_CACHE_MIN_LENGTH:
            continue

        # Look for Jinja-style interpolations.
        jinja_idx = -1
        for marker in ("{{", "{%"):
            idx = prompt.find(marker)
            if idx >= 0 and (jinja_idx < 0 or idx < jinja_idx):
                jinja_idx = idx
        if jinja_idx < 0:
            continue  # No interpolation at all — fully static, fine.

        cutoff = int(len(prompt) * _PROMPT_CACHE_EARLY_FRACTION)
        if jinja_idx >= cutoff:
            continue  # Interpolation only in the tail — prefix is stable.

        nid = node.get("id") or ""
        out.append(Lint(
            code="prompt_cache_breakage",
            severity="warn",
            message=(
                f"Node `{nid}` (“{label}”) has Jinja interpolation "
                f"`{prompt[jinja_idx:jinja_idx+20]}…` near the top of a "
                f"{len(prompt)}-char `systemPrompt` — Vertex/Anthropic "
                "prefix cache will miss on every turn."
            ),
            fix_hint=(
                "Move per-turn variables ({{ trigger.x }}, {{ node_y.json.z }}) "
                "into a `=== CURRENT TURN CONTEXT ===` block at the END of the "
                "prompt; keep all rules and static context BEFORE that block "
                "byte-stable across turns. Pattern reference: "
                "build_ae_ops_workflow_v10.py::WORKER_PROMPT_STATIC + "
                "WORKER_PROMPT_DYNAMIC."
            ),
            node_id=nid,
        ))
    return out


# Display-name patterns that imply read-only / observation-only roles.
# When a ReAct Agent's display name matches these, an unset
# `allowedToolCategories` is suspicious — the prompt likely claims
# read-only but nothing enforces it. (V10 Verifier ships
# `allowedToolCategories: ["read", "case"]`.)
_READ_ONLY_ROLE_PATTERNS = (
    "verifier", "verify", "critic", "reviewer", "review",
    "validator", "validate", "checker", "check",
    "auditor", "audit", "evaluator", "evaluate", "judge",
    "observer", "observe", "watcher", "watch",
    "monitor", "inspector", "inspect",
)

# Display-name patterns that imply primary action role. A worker with
# maxIterations < 2 is almost certainly mis-configured — one iteration
# means a single LLM call with no chance to chain a tool result back
# in. Maps to roles that DO call tools, not the read-only ones above.
_WORKER_ROLE_PATTERNS = (
    "worker", "investigator", "investigate", "diagnose", "diagnos",
    "remediator", "remediate", "executor", "execute", "operator",
    "agent", "orchestrator",
)


def lint_react_role_inconsistency(graph: dict[str, Any]) -> list[Lint]:
    """Warn when a ReAct Agent's role (per displayName) doesn't match
    its config.

    Two patterns flagged:

      1. Read-only role + no `allowedToolCategories` — the prompt may
         claim "you have only read-only tools" but the engine doesn't
         enforce that without the allowlist. A poorly-prompted Verifier
         could still call `restart_service` if SMART-06 ranked it high.
         V10 closed this gap; this lint nudges other ReAct nodes to
         follow suit.

      2. Worker / executor role with `maxIterations < 2` — only one
         iteration means no tool chaining possible. Almost always a
         configuration mistake.

    Both warn-severity (the workflow will still run; it just won't do
    what the author probably wants).
    """
    out: list[Lint] = []
    for node in _nodes(graph):
        data = node.get("data") or {}
        if (data.get("label") or "") != "ReAct Agent":
            continue
        nid = node.get("id") or ""
        config = data.get("config") or {}
        display = (data.get("displayName") or "").lower()
        if not display:
            continue

        is_read_only_role = any(p in display for p in _READ_ONLY_ROLE_PATTERNS)
        is_worker_role = any(p in display for p in _WORKER_ROLE_PATTERNS) and not is_read_only_role
        allowed_categories = config.get("allowedToolCategories") or config.get(
            "allowed_tool_categories"
        )

        if is_read_only_role and not allowed_categories:
            out.append(Lint(
                code="react_role_no_category_restriction",
                severity="warn",
                message=(
                    f"ReAct Agent `{nid}` (“{data.get('displayName')}”) looks "
                    "like a read-only role but has no `allowedToolCategories` — "
                    "the prompt's read-only claim is not enforced by the engine."
                ),
                fix_hint=(
                    "Set `allowedToolCategories: [\"read\", \"case\"]` on the "
                    "node config so the engine's tool filter drops "
                    "remediation tools before the model sees them. "
                    "Categories: case / glossary / web / remediation / read."
                ),
                node_id=nid,
            ))

        if is_worker_role:
            try:
                max_iter = int(config.get("maxIterations", 0) or 0)
            except (TypeError, ValueError):
                max_iter = 0
            if 0 < max_iter < 2:
                out.append(Lint(
                    code="react_worker_iterations_too_low",
                    severity="warn",
                    message=(
                        f"ReAct Agent `{nid}` (“{data.get('displayName')}”) "
                        f"has maxIterations={max_iter} — one iteration can't "
                        "chain a tool result back into the loop, so this is "
                        "effectively a single-shot LLM call."
                    ),
                    fix_hint=(
                        "Bump `maxIterations` to at least 3 (typical workers "
                        "use 5; verifiers use 2). For a single-shot LLM, use "
                        "`LLM Agent` instead of `ReAct Agent`."
                    ),
                    node_id=nid,
                ))
    return out
