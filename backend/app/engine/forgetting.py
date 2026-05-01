"""CTX-MGMT.M — forgetting / decay helpers.

Two pieces:

  * **End-of-run output retention** — when a node config sets
    ``data.config.retainOutputAfterRun: false``, the engine clears
    that node's slot from ``context`` at end-of-run BEFORE
    persisting ``instance.context_json``. Useful for nodes whose
    output is only meant for the next downstream node, never for
    retrospective inspection (e.g. a transient HTTP response that
    a downstream LLM summarised, after which the raw response
    is dead weight).
  * **Checkpoint retention sweep** — operator utility
    ``prune_aged_checkpoints(db, older_than_days)`` deletes
    ``InstanceCheckpoint`` rows older than the threshold. Same
    shape as ``cleanup_ephemeral_workflows`` (CTX-MGMT.A's sibling
    helper). Per-tenant retention via
    ``tenant_policies.checkpoint_retention_days`` (NULL =
    SYSTEM_DEFAULT_RETENTION_DAYS).

Anthropic / agent-memory literature: forgetting is first-class.
Time-based decay + explicit deletion are part of the design,
not afterthoughts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Default checkpoint retention when no per-tenant override is set.
# 30 days is enough for most replay / debug needs without
# accumulating runaway storage on long-lived tenants.
SYSTEM_DEFAULT_RETENTION_DAYS = 30


def clear_non_retained_outputs(
    context: dict[str, Any],
    nodes_map: dict[str, Any],
) -> list[str]:
    """Walk every node in the graph; for any whose
    ``data.config.retainOutputAfterRun`` is False, pop the
    corresponding slot from ``context``. Called from
    ``execute_graph`` at end-of-run BEFORE the final
    ``_get_clean_context`` write to ``instance.context_json``.

    Returns the list of node ids whose outputs were cleared (for
    logging + observability).

    Default behavior (field unset or True): no-op. Existing graphs
    are unaffected — opt-in by setting the flag to False on the
    nodes whose outputs aren't meant to survive the run.
    """
    cleared: list[str] = []
    for node_id, node in nodes_map.items():
        data = node.get("data") or {}
        config = data.get("config") or {}
        retain = config.get("retainOutputAfterRun")
        # Default True — only act when explicitly False.
        if retain is False and node_id in context:
            context.pop(node_id, None)
            cleared.append(node_id)
            # Also clear the alias slot if exposeAs was set
            # (CTX-MGMT.C). Otherwise the alias would carry the
            # ghost value into context_json.
            alias = config.get("exposeAs")
            if isinstance(alias, str) and alias.strip() and alias.strip() in context:
                context.pop(alias.strip(), None)
                cleared.append(f"{alias.strip()} (alias)")
    return cleared


def resolve_checkpoint_retention_days(
    db: Session,
    *,
    tenant_id: str,
) -> int:
    """Resolve per-tenant checkpoint retention. NULL on the policy
    row → system default. Defensive on lookup error — returns the
    system default rather than 0 (which would delete everything
    immediately)."""
    try:
        from app.engine.tenant_policy_resolver import get_effective_policy
        policy = get_effective_policy(tenant_id)
        days = getattr(policy, "checkpoint_retention_days", None)
        if isinstance(days, int) and days > 0:
            return days
    except Exception as exc:
        logger.warning(
            "checkpoint_retention: tenant policy lookup failed for "
            "tenant=%r (%s); using system default %d days",
            tenant_id, exc, SYSTEM_DEFAULT_RETENTION_DAYS,
        )
    return SYSTEM_DEFAULT_RETENTION_DAYS


def prune_aged_checkpoints(
    db: Session,
    *,
    tenant_id: str | None = None,
    older_than_days: int | None = None,
) -> int:
    """Delete ``InstanceCheckpoint`` rows older than the retention
    threshold. Returns the number of rows deleted.

    Args
    ----
    ``tenant_id``: when set, only sweep checkpoints for that tenant
                   (uses tenant policy's `checkpoint_retention_days`
                   override if set). When None, sweep across all
                   tenants using the system default.
    ``older_than_days``: explicit override of the threshold. Wins
                        over the per-tenant policy when set.

    Operator utility — same shape as
    ``cleanup_ephemeral_workflows``. Wire to a Beat task in a
    follow-up; for now it's available as a manual / scheduled job.
    """
    from app.models.workflow import InstanceCheckpoint, WorkflowInstance

    if older_than_days is None:
        if tenant_id is not None:
            older_than_days = resolve_checkpoint_retention_days(db, tenant_id=tenant_id)
        else:
            older_than_days = SYSTEM_DEFAULT_RETENTION_DAYS

    if older_than_days <= 0:
        # Defensive — refuse to delete everything.
        logger.warning(
            "prune_aged_checkpoints: threshold %d ≤ 0; refusing to "
            "delete (would wipe every checkpoint)",
            older_than_days,
        )
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    # Build the query. Join through WorkflowInstance for tenant
    # filtering when a tenant_id is supplied.
    query = db.query(InstanceCheckpoint).filter(
        InstanceCheckpoint.saved_at < cutoff,
    )
    if tenant_id is not None:
        query = query.join(WorkflowInstance,
                           WorkflowInstance.id == InstanceCheckpoint.instance_id).filter(
            WorkflowInstance.tenant_id == tenant_id,
        )

    # Find ids first, then delete by id — portable across PG versions
    # and avoids weird subquery DELETE semantics.
    doomed_ids = [row.id for row in query.with_entities(InstanceCheckpoint.id).all()]
    if not doomed_ids:
        return 0

    deleted = (
        db.query(InstanceCheckpoint)
        .filter(InstanceCheckpoint.id.in_(doomed_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info(
        "prune_aged_checkpoints: deleted %d checkpoint(s) older than "
        "%d days (tenant=%s)",
        deleted, older_than_days, tenant_id or "<all>",
    )
    return int(deleted or 0)
