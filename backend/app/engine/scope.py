"""CTX-MGMT.C v2.a — runtime enforcement of ``dependsOn`` for Jinja
renders.

C v1 (already shipped) made ``dependsOn`` informational: the static
lint ``jinja_ref_outside_depends_on`` warns at promote time when a
template ref escapes the declared list, but the runtime still
handed the full ``context`` dict to every render. v2.a flips that —
when a node declares ``dependsOn``, ``render_prompt`` only sees:

  * The listed ``node_*`` slots.
  * The ``exposeAs`` aliases of those slots (so dependsOn=["node_a"]
    still resolves ``{{ case.id }}`` when node_a's exposeAs is
    "case").
  * Every non-``node_*`` infrastructure key (``trigger``, ``_runtime``,
    ``_loop_*``, conv-memory keys, distill aliases of declared
    nodes, etc.).

When ``dependsOn`` is unset the helper returns ``context``
unchanged — backward-compatible for every graph that hasn't
declared the field. The lint already runs at promote time, so
authors see the issue before runtime bites them.

Why a separate module
---------------------

The build_scoped_safe_context helper is consumed by render_prompt
and (in upcoming v2.b) the safe_eval call sites. Putting it here
keeps both consumers anchored to one source of truth and lets the
unit tests cover the helper without spinning up Jinja or the
engine.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# CTX-MGMT.C v2.a — per-thread stash of the currently-dispatching
# node. The runner sets ``_dispatch_state.node_data`` before each
# ``dispatch_node`` call (sequential AND parallel-branch threads)
# and ``render_prompt`` reads it back when an explicit kwarg isn't
# given. Thread-local because the parallel-branch executor runs
# multiple ``dispatch_node`` calls concurrently against the same
# ``context``; storing on ``context`` would race between sibling
# branches.
_dispatch_state = threading.local()


def set_current_node_data(node_data: Any) -> None:
    """Stash the currently-dispatching node on the calling thread.
    Called by the runner around each ``dispatch_node`` invocation.
    Idempotent overwrite; no-op if called outside a runner thread."""
    _dispatch_state.node_data = node_data


def get_current_node_data() -> Any:
    """Read the per-thread current-node stash. Returns ``None``
    when no runner has stashed for this thread (tests / direct
    callers / threads outside the engine)."""
    return getattr(_dispatch_state, "node_data", None)


def clear_current_node_data() -> None:
    """Drop the per-thread stash. Optional — the runner overwrites
    on every dispatch — but useful in tests for hygiene."""
    _dispatch_state.node_data = None


def get_depends_on(node_data: Any) -> list[str] | None:
    """Return the validated ``dependsOn`` list (declared on the node's
    config) or ``None`` if the field is unset / malformed.

    A returned ``[]`` (empty list) is meaningful — it means the
    author explicitly declared "no upstream node visible", and the
    runtime should honor that.
    """
    if not isinstance(node_data, dict):
        return None
    config = node_data.get("config")
    if config is None:
        # `node_data` may be the wrapped {data: {config: ...}} shape
        # used by the workflow definition; unwrap one level when
        # config isn't at the top.
        data = node_data.get("data")
        if isinstance(data, dict):
            config = data.get("config")
    if not isinstance(config, dict):
        return None
    raw = config.get("dependsOn")
    if not isinstance(raw, list):
        return None
    return [str(d).strip() for d in raw if isinstance(d, str) and d.strip()]


def _node_config(node: Any) -> dict[str, Any]:
    """Extract the config dict from a node, handling both shapes:
    ``{config: {...}}`` (handler-input shape) and
    ``{data: {config: {...}}}`` (graph-definition shape).
    """
    if not isinstance(node, dict):
        return {}
    cfg = node.get("config")
    if isinstance(cfg, dict):
        return cfg
    data = node.get("data")
    if isinstance(data, dict):
        cfg = data.get("config")
        if isinstance(cfg, dict):
            return cfg
    return {}


def collect_alias_index(nodes_map: dict[str, Any] | None) -> dict[str, str]:
    """Walk ``nodes_map`` and return ``{alias: source_node_id}`` for
    every declared ``exposeAs``. Used by the scope filter to decide
    which non-``node_*`` keys are aliases (and therefore subject to
    dependsOn filtering) vs which are infrastructure keys.
    """
    out: dict[str, str] = {}
    if not isinstance(nodes_map, dict):
        return out
    for nid, node in nodes_map.items():
        cfg = _node_config(node)
        alias = cfg.get("exposeAs")
        if isinstance(alias, str) and alias.strip():
            out[alias.strip()] = str(nid)
    return out


def _visible_keys_for(
    deps: list[str],
    alias_index: dict[str, str],
) -> set[str]:
    """Return the set of context keys visible to a node with the
    given dependsOn list. Includes the dep node ids themselves AND
    the aliases of those nodes (resolved via ``alias_index``)."""
    visible: set[str] = set(deps)
    for alias, source_nid in alias_index.items():
        if source_nid in visible:
            visible.add(alias)
    return visible


def build_scoped_safe_context(
    context: dict[str, Any],
    node_data: Any,
    nodes_map: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a shallow-filtered view of ``context`` for Jinja.

    Behavior:
      * ``dependsOn`` unset → returns ``context`` unchanged. The hot
        path. Existing graphs see no behavior change.
      * ``dependsOn`` declared (including ``[]``) → returns a new
        dict containing only the visible keys. ``_runtime``,
        ``trigger``, ``_loop_*`` and other infrastructure keys are
        always preserved. ``node_*`` slots are kept only when in
        the dependsOn list. Non-``node_*`` keys that are known
        ``exposeAs`` aliases are kept only when their source node is
        in dependsOn; non-``node_*`` keys that aren't aliases (i.e.
        true infrastructure) are always kept.

    Pure helper — no I/O, no engine state. Test-friendly.
    """
    deps = get_depends_on(node_data)
    if deps is None:
        return context

    alias_index = collect_alias_index(nodes_map)
    visible = _visible_keys_for(deps, alias_index)

    out: dict[str, Any] = {}
    for k, v in context.items():
        if k.startswith("node_"):
            if k in visible:
                out[k] = v
            continue
        # Non-node_* key. Either an alias (filterable) or
        # infrastructure (always visible).
        if k in alias_index and k not in visible:
            # Known alias of a node NOT in dependsOn — drop.
            continue
        out[k] = v
    return out


def is_scope_enforced(node_data: Any) -> bool:
    """Convenience: True iff the node declares ``dependsOn`` (so
    runtime scope filtering should kick in)."""
    return get_depends_on(node_data) is not None
