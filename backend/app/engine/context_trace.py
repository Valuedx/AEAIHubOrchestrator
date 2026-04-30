"""CTX-MGMT.H — context-write tracing.

Append-only event log of every ``context[node_id] = output`` write
the engine performs. Used by the copilot's ``inspect_context_flow``
runner tool to answer "where did node_X come from?" /
"which node wrote to slot Y?" without forcing the user to scrape
``instance.context_json`` by hand.

v1 tracks WRITES only. Reads + misses are deferred to v2 — the
volume is template-dependent (one Jinja prompt can hit hundreds of
attribute reads), and the missing-key story is better served by a
static lint at promote time (CTX-MGMT.B).

Gating
------

Tracing is OFF by default for production. Two paths turn it ON:

  * **Ephemeral instances** (copilot-initiated `execute_draft` /
    `run_scenario` / `run_debug_scenario` runs) — always traced.
    The copilot debug tools need the data and ephemeral instances
    are short-lived + cleaned up by the existing
    ``cleanup_ephemeral_workflows`` Beat task.
  * **Tenant policy** (``tenant_policies.context_trace_enabled``) —
    opt-in for production debugging.

The runner stamps the resolved enabled-ness once at ``execute_graph``
start into ``context["_runtime"]["context_trace_enabled"]``. The
``record_write`` fast path checks that flag with a single dict
lookup — when disabled, returns immediately without touching the DB.

Per-instance cap
----------------

500 events per instance. When exceeded, ``record_write`` deletes the
oldest rows for the instance before inserting the new one. This
keeps a malicious or buggy graph from filling the trace table.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Per-instance row cap. 500 events is enough for ~5 turns of a 28-node
# V10-shape graph (5 × 28 ≈ 140 writes); leaves headroom for retries
# and the future cyclic-graph aggregation cases.
PER_INSTANCE_TRACE_CAP = 500

# When the cap is hit, drop this many oldest rows in one shot rather
# than deleting one-at-a-time on every overflow write. Reduces DB
# round-trips for chatty instances.
TRACE_TRIM_BATCH = 50


def is_trace_enabled(context: dict[str, Any]) -> bool:
    """Single-lookup fast-path check. Returns False unless the runner
    explicitly enabled tracing for this instance.

    Read from ``context["_runtime"]["context_trace_enabled"]`` so the
    flag survives suspend/resume (it's set once at execute_graph
    entry and the resume paths re-hoist it via _hoist_legacy_runtime
    if needed)."""
    runtime = context.get("_runtime") if isinstance(context, dict) else None
    if not isinstance(runtime, dict):
        return False
    return bool(runtime.get("context_trace_enabled"))


def resolve_trace_flag(
    db: Session,
    *,
    tenant_id: str,
    is_ephemeral: bool,
) -> bool:
    """Resolve whether tracing should be on for this instance.

    Ephemeral (copilot-initiated) instances always trace.
    Production: read ``tenant_policies.context_trace_enabled``;
    default False on missing/error so production never traces by
    accident.
    """
    if is_ephemeral:
        return True
    try:
        from app.engine.tenant_policy_resolver import get_effective_policy
        policy = get_effective_policy(tenant_id)
        return bool(getattr(policy, "context_trace_enabled", False))
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "context_trace: tenant policy lookup failed for tenant=%r (%s); "
            "leaving trace off for this run",
            tenant_id, exc,
        )
        return False


def record_write(
    db: Session,
    context: dict[str, Any],
    *,
    tenant_id: str,
    instance_id: Any,
    node_id: str,
    size_bytes: int | None,
    reducer: str | None,
    overflowed: bool,
) -> None:
    """Record one write event. Fast no-op when tracing is disabled
    for the instance.

    Caller owns the commit boundary — we ``flush()`` so the row is
    written before the runner's next checkpoint commit. Failures
    here are logged but never raised (the trace is observability,
    not a correctness requirement).
    """
    if not is_trace_enabled(context):
        return

    try:
        from app.models.workflow import InstanceContextTrace

        # Cap enforcement — when the count for this instance would
        # exceed PER_INSTANCE_TRACE_CAP, drop the oldest TRACE_TRIM_BATCH
        # rows. Use a single DELETE to keep round-trips bounded.
        # Skip the count if we already trimmed recently (tracked on
        # _runtime so this stays cheap).
        runtime = context.setdefault("_runtime", {})
        last_trimmed_at = int(runtime.get("ctxtrace_count_since_trim", 0))
        runtime["ctxtrace_count_since_trim"] = last_trimmed_at + 1
        if last_trimmed_at >= TRACE_TRIM_BATCH:
            current_count = (
                db.query(InstanceContextTrace)
                .filter_by(instance_id=instance_id)
                .count()
            )
            if current_count >= PER_INSTANCE_TRACE_CAP:
                _trim_oldest(db, instance_id=instance_id)
            runtime["ctxtrace_count_since_trim"] = 0

        row = InstanceContextTrace(
            id=_uuid.uuid4(),
            tenant_id=tenant_id,
            instance_id=instance_id,
            node_id=node_id,
            op="write",
            key=node_id,  # for writes the slot key IS the node id
            size_bytes=size_bytes,
            reducer=reducer,
            overflowed=overflowed,
        )
        db.add(row)
        db.flush()
    except Exception as exc:
        logger.warning(
            "context_trace.record_write failed (node=%s, instance=%s): %s",
            node_id, instance_id, exc,
        )


def _trim_oldest(db: Session, *, instance_id: Any) -> int:
    """Delete the oldest TRACE_TRIM_BATCH events for the instance.
    Returns the number of rows deleted. Used when the per-instance
    cap is hit."""
    from app.models.workflow import InstanceContextTrace

    # Find the oldest TRACE_TRIM_BATCH ids, then delete by id.
    # Subquery DELETE patterns differ across PostgreSQL versions, so
    # the explicit two-step is portable.
    oldest_ids = [
        row.id for row in (
            db.query(InstanceContextTrace.id)
            .filter_by(instance_id=instance_id)
            .order_by(InstanceContextTrace.ts.asc())
            .limit(TRACE_TRIM_BATCH)
            .all()
        )
    ]
    if not oldest_ids:
        return 0
    deleted = (
        db.query(InstanceContextTrace)
        .filter(InstanceContextTrace.id.in_(oldest_ids))
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


def fetch_events_for_instance(
    db: Session,
    *,
    tenant_id: str,
    instance_id: Any,
    key: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read events for one instance, optionally filtered by key
    (or key prefix when key ends with ``*``).

    Used by ``copilot.runner_tools.inspect_context_flow``. RLS
    handles tenant scoping; the explicit ``tenant_id`` filter is
    defence in depth.
    """
    from app.models.workflow import InstanceContextTrace

    query = (
        db.query(InstanceContextTrace)
        .filter_by(tenant_id=tenant_id, instance_id=instance_id)
    )
    if key:
        if key.endswith("*"):
            prefix = key[:-1]
            query = query.filter(InstanceContextTrace.key.like(f"{prefix}%"))
        else:
            query = query.filter_by(key=key)

    rows = (
        query
        .order_by(InstanceContextTrace.ts.asc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [
        {
            "id": str(r.id),
            "node_id": r.node_id,
            "op": r.op,
            "key": r.key,
            "size_bytes": r.size_bytes,
            "reducer": r.reducer,
            "overflowed": r.overflowed,
            "ts": r.ts.isoformat() if r.ts else None,
        }
        for r in rows
    ]
