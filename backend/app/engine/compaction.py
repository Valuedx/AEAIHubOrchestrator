"""CTX-MGMT.K — within-run compaction pass.

When `context_json`'s estimated size exceeds the compaction threshold
(default 128 kB; per-tenant override via
`tenant_policies.context_compaction_enabled`), the engine compacts
the oldest in-context node outputs by replacing them with the same
overflow-stub shape used for CTX-MGMT.A, persisting the full output
to ``node_output_artifacts``.

This is Anthropic's "first lever" for context engineering applied to
within-run state: *"taking a conversation nearing the context window
limit, summarizing its contents, and reinitiating a new context
window with the summary"*. We do the equivalent for the workflow's
runtime context.

Why oldest-first
----------------

We don't have read-event tracking yet (CTX-MGMT.H v1 ships writes
only; v2 adds reads + misses). The proxy used here is **write age**
— a node written 25 hops ago is more likely to be cold than a node
written 1 hop ago. Imperfect, but unblocks compaction without
waiting for read tracking.

When CTX-MGMT.H v2 lands, the selection function will switch to
``last_read_age`` (longer-since-read = more likely to be cold).

What gets compacted
-------------------

  * Skipped: outputs already overflow-stubbed (CTX-MGMT.A) or
    compacted on a prior pass — no double work.
  * Skipped: outputs smaller than ``MIN_NODE_SIZE_FOR_COMPACTION``
    — compacting a 100 byte output saves nothing meaningful and
    loses the inline value's full-fidelity rendering.
  * Skipped: ``trigger`` and any non-``node_*`` slot — those carry
    workflow-level state that downstream Jinja almost always reaches
    for.
  * Selected: ``node_*`` slots whose value is a non-stub dict and
    whose serialised size is at least ``MIN_NODE_SIZE_FOR_COMPACTION``.

Selection order
---------------

We don't have explicit per-node write timestamps in the runtime
state today (the trace table has them but reading it on every
compaction pass is a DB hit per pass). Instead the engine maintains
``_runtime["written_node_order"]`` — a list of node ids in
insertion order, updated on every write. Compaction iterates this
list from the front (oldest first) until size is back under the
target.

Approximate cumulative size
---------------------------

Re-encoding the full clean context to JSON after every node write
is O(N²) in graph size. Instead we maintain
``_runtime["context_size_bytes"]`` — cumulative size approximation
updated as each write happens. Compaction subtracts the freed bytes.
A periodic full reconciliation (every M writes) corrects drift.

Public surface
--------------

  * ``COMPACTION_THRESHOLD_BYTES``, ``COMPACTION_TARGET_BYTES``,
    ``MIN_NODE_SIZE_FOR_COMPACTION`` — tunables.
  * ``track_write_size(runtime, node_id, size_bytes)`` — bookkeeping
    helper called from the engine after each write.
  * ``maybe_compact(db, context, ...)`` — the end-to-end pass.
    Fast no-op when below threshold OR tenant-disabled.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.engine.output_artifact import (
    estimate_output_size,
    materialize_overflow_stub,
    persist_artifact,
)

logger = logging.getLogger(__name__)


# Trigger threshold — total clean context approaches this size, run a
# pass. 128 kB is twice the per-node default budget (CTX-MGMT.A's
# 64 kB) so a workflow can have ~2 large nodes inline before
# compaction kicks in.
COMPACTION_THRESHOLD_BYTES = 128 * 1024

# Target size for the compaction pass — compact until size drops
# below this, then stop. Set lower than the trigger so we don't
# bounce-compact on every node write right at the threshold.
COMPACTION_TARGET_BYTES = 96 * 1024

# Don't compact tiny outputs — the savings are negligible and we
# lose full-fidelity inline rendering for downstream Jinja.
MIN_NODE_SIZE_FOR_COMPACTION = 1024  # 1 kB

# Reconciliation interval — every N node writes, recompute the full
# clean-context size to correct drift in the running approximation.
RECONCILIATION_INTERVAL = 25

# Keys we never compact even when they're large. These carry
# workflow-level state Jinja almost always reaches for.
_NEVER_COMPACT_KEYS = frozenset({
    "trigger",
    "approval",
    "orchestrator_user_reply",
})


def is_compaction_enabled(context: dict[str, Any]) -> bool:
    """Single-dict-lookup fast path. Read from
    ``_runtime["context_compaction_enabled"]`` so the flag survives
    suspend/resume."""
    runtime = context.get("_runtime") if isinstance(context, dict) else None
    if not isinstance(runtime, dict):
        return False
    return bool(runtime.get("context_compaction_enabled"))


def resolve_compaction_flag(
    db: Session,
    *,
    tenant_id: str,
) -> bool:
    """Default ON, opt out via ``tenant_policies.context_compaction_enabled``.

    Default opposite of the trace flag (which is opt-in for prod) —
    compaction is a cost saver, so default-on is the right choice
    for the common case. Tenants with strict audit-trail
    requirements can opt out and get full-fidelity replay at the
    cost of larger ``context_json``.
    """
    try:
        from app.engine.tenant_policy_resolver import get_effective_policy
        policy = get_effective_policy(tenant_id)
        # Backward-compat: the column is added by migration 0037; for
        # databases that haven't migrated yet `getattr` returns the
        # default. Default True so most tenants compact automatically.
        return bool(getattr(policy, "context_compaction_enabled", True))
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "compaction: tenant policy lookup failed for tenant=%r (%s); "
            "defaulting compaction ON",
            tenant_id, exc,
        )
        return True


def track_write_size(
    runtime: dict[str, Any],
    node_id: str,
    size_bytes: int,
) -> None:
    """Bookkeeping helper called after each ``context[node_id] =
    output`` write. Updates the running cumulative size approximation
    and the insertion-order list used by compaction's selection.

    Idempotent on the order list: if the same node id is written
    twice (e.g. a loop body), it moves to the END of the order
    list — recently-written = least likely to be a compaction target.
    """
    # Cumulative-size approximation. A repeated write to the same
    # slot SHOULD subtract the prior value's size first; we track the
    # last-written size per node so this stays accurate enough.
    sizes = runtime.setdefault("written_node_sizes", {})
    prior_size = sizes.get(node_id, 0)
    runtime["context_size_bytes"] = (
        max(0, int(runtime.get("context_size_bytes", 0)) - int(prior_size))
        + int(size_bytes)
    )
    sizes[node_id] = int(size_bytes)

    # Insertion-order list. Move to end on rewrite.
    order = runtime.setdefault("written_node_order", [])
    if node_id in order:
        order.remove(node_id)
    order.append(node_id)


def reconcile_size(context: dict[str, Any]) -> int:
    """Recompute the cumulative size from the current clean context.
    Updates ``_runtime["context_size_bytes"]`` and returns the new
    total. Called periodically to correct drift in the running
    approximation."""
    runtime = context.get("_runtime") if isinstance(context, dict) else None
    if not isinstance(runtime, dict):
        return 0
    # Walk node_* slots only; trigger + non-node keys aren't subject
    # to compaction so they don't count toward the budget.
    total = 0
    sizes = {}
    for k, v in context.items():
        if not isinstance(k, str) or not k.startswith("node_"):
            continue
        size = estimate_output_size(v)
        sizes[k] = size
        total += size
    runtime["context_size_bytes"] = total
    runtime["written_node_sizes"] = sizes
    runtime["writes_since_reconcile"] = 0
    return total


def _is_already_stubbed(value: Any) -> bool:
    """True iff ``value`` is already an overflow or compaction stub
    (skip — no double work)."""
    if not isinstance(value, dict):
        return False
    return bool(value.get("_overflow") or value.get("_compacted"))


def _pick_compaction_candidates(
    context: dict[str, Any],
    runtime: dict[str, Any],
) -> list[tuple[str, int]]:
    """Return ``[(node_id, size_bytes), ...]`` ordered oldest-first
    among slots eligible for compaction:

      * key starts with ``node_``
      * value is a dict (compactable shape)
      * value is NOT already an overflow / compaction stub
      * size >= MIN_NODE_SIZE_FOR_COMPACTION
      * not in _NEVER_COMPACT_KEYS

    Iterates ``_runtime["written_node_order"]`` so insertion order
    drives selection. Skips entries that no longer satisfy the
    eligibility criteria.
    """
    order = runtime.get("written_node_order") or []
    sizes = runtime.get("written_node_sizes") or {}
    candidates: list[tuple[str, int]] = []
    for nid in order:
        if nid in _NEVER_COMPACT_KEYS:
            continue
        if not isinstance(nid, str) or not nid.startswith("node_"):
            continue
        value = context.get(nid)
        if not isinstance(value, dict):
            continue
        if _is_already_stubbed(value):
            continue
        size = sizes.get(nid)
        if size is None:
            size = estimate_output_size(value)
        if size < MIN_NODE_SIZE_FOR_COMPACTION:
            continue
        candidates.append((nid, int(size)))
    return candidates


def maybe_compact(
    db: Session,
    context: dict[str, Any],
    *,
    tenant_id: str,
    instance_id: Any,
    threshold: int = COMPACTION_THRESHOLD_BYTES,
    target: int = COMPACTION_TARGET_BYTES,
) -> dict[str, Any] | None:
    """Run a compaction pass if the cumulative context size exceeds
    ``threshold``. Compacts oldest-first until size drops below
    ``target`` or candidates are exhausted.

    Returns ``None`` when no compaction was needed; otherwise a
    metadata dict::

        {
          "compacted_node_ids": [...],
          "bytes_freed": N,
          "size_before": ..., "size_after": ...,
          "artifacts": [{node_id, artifact_id, size_bytes}, ...]
        }

    Fast-path: if compaction is disabled OR the running size
    approximation is below threshold, returns immediately without
    touching the DB or walking candidates.
    """
    if not is_compaction_enabled(context):
        return None
    runtime = context.get("_runtime") if isinstance(context, dict) else None
    if not isinstance(runtime, dict):
        return None

    # Periodic reconciliation: every N writes, recompute the running
    # total to correct drift. Keeps the fast-path approximation
    # honest without paying the O(N) cost on every node.
    writes_since = int(runtime.get("writes_since_reconcile", 0)) + 1
    runtime["writes_since_reconcile"] = writes_since
    if writes_since >= RECONCILIATION_INTERVAL:
        reconcile_size(context)

    cur_size = int(runtime.get("context_size_bytes", 0))
    if cur_size <= threshold:
        return None

    # We're over threshold — pick candidates and compact until we're
    # back under target. The reconciliation above just refreshed
    # the running size if the interval hit; if not, the approximation
    # may have drifted, but oldest-first still makes the right
    # selection regardless of absolute size accuracy.
    candidates = _pick_compaction_candidates(context, runtime)
    if not candidates:
        # Nothing to compact (everything is small / already stubbed).
        return None

    compacted_ids: list[str] = []
    artifacts: list[dict[str, Any]] = []
    bytes_freed = 0
    size_before = cur_size

    for nid, size in candidates:
        if cur_size <= target:
            break
        original = context.get(nid)
        if not isinstance(original, dict):
            continue
        # Persist the full output to node_output_artifacts (same
        # table CTX-MGMT.A uses) so the copilot's
        # inspect_node_artifact tool can read it.
        try:
            artifact_id = persist_artifact(
                db,
                tenant_id=tenant_id,
                instance_id=instance_id,
                node_id=nid,
                output=original,
                size_bytes=size,
                budget_bytes=threshold,  # surfaces "compacted at this threshold" for audit
            )
        except Exception as exc:
            logger.warning(
                "compaction: persist_artifact failed for node=%s: %s — skipping",
                nid, exc,
            )
            continue

        stub = materialize_overflow_stub(
            original,
            budget=threshold,
            size_bytes=size,
            artifact_id=artifact_id,
            kind="compaction",
        )
        stub_size = estimate_output_size(stub)
        context[nid] = stub
        bytes_freed += max(0, size - stub_size)
        cur_size = max(0, cur_size - (size - stub_size))
        compacted_ids.append(nid)
        artifacts.append({
            "node_id": nid,
            "artifact_id": artifact_id,
            "original_size_bytes": size,
            "stub_size_bytes": stub_size,
        })

        # Update the size tracker for this slot.
        sizes = runtime.setdefault("written_node_sizes", {})
        sizes[nid] = stub_size

    if not compacted_ids:
        return None

    runtime["context_size_bytes"] = cur_size
    logger.info(
        "compaction: instance=%s freed %d bytes across %d nodes "
        "(size before=%d, after=%d, target=%d)",
        instance_id, bytes_freed, len(compacted_ids),
        size_before, cur_size, target,
    )
    return {
        "compacted_node_ids": compacted_ids,
        "bytes_freed": bytes_freed,
        "size_before": size_before,
        "size_after": cur_size,
        "artifacts": artifacts,
    }
