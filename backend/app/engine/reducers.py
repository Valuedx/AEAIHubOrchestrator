"""CTX-MGMT.L — per-node output reducers (state-channel reducers).

Pure helpers. Given the current value in ``context[node_id]`` (or
``None`` if absent) and a new output, return the merged value
according to the node's configured reducer.

Why this exists
---------------

Today the engine writes ``context[node_id] = output`` — last-write-
wins. That's correct for the common case (one node fires once) but
forces ad-hoc aggregation logic for:

  * **ForEach / Loop body nodes** that produce one output per iteration
    — today the runner clears the slot per iteration and aggregates
    post-loop into a hand-coded ``{loop_results: [...], iterations: N}``
    shape.
  * **Parallel branches converging on a Coalesce node** (CTX-MGMT.E,
    in the plan) — needs append semantics natively.
  * **Append-only audit trails** like a shared-evidence channel where
    multiple sub-workflows promote findings to the parent.
  * **Counters / max-trackers / min-trackers** — today implementing
    these requires a Code node or hand-rolled merge.

LangGraph 2026 calls this the *reducer / state-channel* model:
*"key is operator.add, which tells LangGraph to append new messages
to the existing list instead of overwriting"*. We adopt the same
shape — each node declares how its slot's successive writes combine.

Reducer registry
----------------

  * ``overwrite`` — last-write-wins. The default. Identical to
    today's behavior; no migration needed for existing graphs.
  * ``append`` — each write appends to a list. Auto-init from None
    to []; if the current value is non-list, wraps it as the first
    element before appending. Common case: ForEach body node, audit
    trails, parallel branch convergence.
  * ``merge`` — dict.update semantics; new keys add, existing keys
    overwrite. Both current and new must be dicts (or None for
    current). Useful for: structured payload built up across nodes.
  * ``max`` / ``min`` — numeric comparison; replaces with max / min
    of current and new. None is treated as "no current value".
  * ``counter`` — integer accumulation (``current + new``). Both
    must be int-coercible.

Public surface
--------------

  * ``KNOWN_REDUCERS`` — frozen set of valid names.
  * ``DEFAULT_REDUCER`` — ``"overwrite"``.
  * ``resolve_reducer(node_data)`` — pull the reducer name from
    ``data.config.outputReducer``; falls back to default. Unknown
    names also fall back to default with a logged warning (the
    author-time validator catches typos before runtime; this is
    defence in depth).
  * ``apply_reducer(name, current, new)`` — combine and return.

Design rules
------------

  * Pure — no DB, no engine state, no logging side-effects beyond
    one-line warns.
  * Type-tolerant — a reducer mismatch (e.g. ``merge`` on non-dict
    output) logs a warning and falls back to overwrite for that
    specific write rather than raising. Strict validation lives in
    the workflow validator at promote time.
  * Backward-compat — when ``outputReducer`` is unset or invalid,
    behavior is identical to pre-CTX-MGMT.L (last-write-wins).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


KNOWN_REDUCERS = frozenset({
    "overwrite",
    "append",
    "merge",
    "max",
    "min",
    "counter",
})

DEFAULT_REDUCER = "overwrite"


def resolve_reducer(node_data: dict[str, Any] | None) -> str:
    """Pull the reducer name from ``data.config.outputReducer``;
    fall back to ``DEFAULT_REDUCER`` when unset or unknown.

    Logs a warning on unknown names so misconfigured graphs that
    slipped past the validator (or a graph hand-written outside the
    builder) fail loudly in logs rather than silently flipping
    behavior.
    """
    if not isinstance(node_data, dict):
        return DEFAULT_REDUCER
    config = node_data.get("config") or {}
    raw = config.get("outputReducer")
    if not raw:
        return DEFAULT_REDUCER
    name = str(raw).strip().lower()
    if name not in KNOWN_REDUCERS:
        logger.warning(
            "Unknown outputReducer %r — falling back to %r. "
            "Valid reducers: %s",
            raw, DEFAULT_REDUCER, sorted(KNOWN_REDUCERS),
        )
        return DEFAULT_REDUCER
    return name


def apply_reducer(name: str, current: Any, new: Any) -> Any:
    """Combine ``current`` and ``new`` according to the named reducer.

    The standard contract: if the node's slot is being written for
    the first time, ``current`` is ``None``. Reducers must handle
    this — e.g. ``append`` initialises to ``[new]``, ``counter``
    treats ``None`` as 0.

    Type mismatches (e.g. ``merge`` with non-dict ``new``) log a
    warning and fall back to overwrite semantics for that specific
    write. Strict type guarantees should come from the workflow
    validator at promote time, not from runtime crashes.
    """
    if name == "overwrite" or name == DEFAULT_REDUCER:
        return new
    if name == "append":
        return _reduce_append(current, new)
    if name == "merge":
        return _reduce_merge(current, new)
    if name == "max":
        return _reduce_extreme(current, new, op="max")
    if name == "min":
        return _reduce_extreme(current, new, op="min")
    if name == "counter":
        return _reduce_counter(current, new)
    # Unknown reducer at apply time — should have been caught by
    # resolve_reducer, but defence in depth.
    logger.warning("apply_reducer: unknown name %r — using overwrite", name)
    return new


# ---------------------------------------------------------------------------
# Per-reducer implementations
# ---------------------------------------------------------------------------


def _reduce_append(current: Any, new: Any) -> list[Any]:
    """List append semantics. Auto-init from None.

    If ``current`` is non-list (someone reduced over a slot that
    started life with ``overwrite`` semantics), wrap the existing
    value as the first element so we don't lose it. This matters for
    the migration story — flipping a node from ``overwrite`` to
    ``append`` on the next run shouldn't drop the value the slot
    already had.
    """
    if current is None:
        return [new]
    if isinstance(current, list):
        return [*current, new]
    # Wrap legacy non-list values.
    return [current, new]


def _reduce_merge(current: Any, new: Any) -> Any:
    """Dict.update semantics — new keys added, existing keys
    overwritten. Type-tolerant: if new isn't a dict, we log + fall
    back to overwrite for this write."""
    if not isinstance(new, dict):
        logger.warning(
            "merge reducer received non-dict new value (%s) — falling "
            "back to overwrite for this write",
            type(new).__name__,
        )
        return new
    if current is None:
        # Return a shallow copy so mutations on the merged dict don't
        # leak back into the source.
        return dict(new)
    if not isinstance(current, dict):
        logger.warning(
            "merge reducer found non-dict current value (%s) — falling "
            "back to overwrite for this write",
            type(current).__name__,
        )
        return dict(new)
    out = dict(current)
    out.update(new)
    return out


def _coerce_number(value: Any) -> float | None:
    """Try to coerce to float; return None on failure."""
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int — exclude it explicitly so
        # `True > 5` doesn't surprise anyone.
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _reduce_extreme(current: Any, new: Any, *, op: str) -> Any:
    """``max`` / ``min`` reducer. Both must be numeric; non-numeric
    new value falls back to overwrite. None current treats new as
    the seed."""
    new_num = _coerce_number(new)
    if new_num is None:
        logger.warning(
            "%s reducer received non-numeric new value (%s) — falling "
            "back to overwrite",
            op, type(new).__name__,
        )
        return new
    if current is None:
        return new
    cur_num = _coerce_number(current)
    if cur_num is None:
        logger.warning(
            "%s reducer found non-numeric current value (%s) — falling "
            "back to overwrite",
            op, type(current).__name__,
        )
        return new
    if op == "max":
        return new if new_num > cur_num else current
    return new if new_num < cur_num else current


def _reduce_counter(current: Any, new: Any) -> Any:
    """Integer accumulation. ``None`` current = 0; new must be
    int-coercible. Returns int when both inputs are ints, float
    otherwise — preserves the most informative numeric type."""
    new_num = _coerce_number(new)
    if new_num is None:
        logger.warning(
            "counter reducer received non-numeric new value (%s) — "
            "falling back to overwrite",
            type(new).__name__,
        )
        return new
    if current is None:
        # Preserve int type when input is integer-shaped.
        if isinstance(new, bool):
            return int(new)
        if isinstance(new, int):
            return new
        return new_num
    cur_num = _coerce_number(current)
    if cur_num is None:
        logger.warning(
            "counter reducer found non-numeric current value (%s) — "
            "falling back to overwrite",
            type(current).__name__,
        )
        return new
    total = cur_num + new_num
    # Preserve int type when both inputs were integer-shaped.
    if (
        isinstance(current, int) and not isinstance(current, bool)
        and isinstance(new, int) and not isinstance(new, bool)
    ):
        return int(total)
    return total
