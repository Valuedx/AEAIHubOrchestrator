"""Celery Beat schedule trigger service.

Periodically scans workflow definitions for Schedule Trigger nodes and
creates workflow instances for those whose cron expressions are due.
Also prunes old workflow snapshots (V0.9) and archives stale conversation
episodes.

Run alongside the Celery worker:
    celery -A app.workers.celery_app beat --loglevel=info

RLS / tenant-scoping note: every task in this module is fundamentally
cross-tenant — it enumerates workflows, snapshots, or episodes across
every tenant in a single pass — so ``set_tenant_context`` is deliberately
not called on these sessions. Under a non-superuser application role,
Beat MUST be run as a role that bypasses RLS (either a superuser or one
created with ``ALTER ROLE ... BYPASSRLS``). See SETUP_GUIDE.md §5.2a.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from celery.schedules import crontab
from croniter import croniter
from sqlalchemy.exc import IntegrityError

from app.workers.celery_app import celery_app
from app.database import SessionLocal
from app.engine.memory_service import archive_active_episode, resolve_episode_policy
from app.models.memory import ConversationEpisode
from app.models.workflow import (
    AsyncJob,
    ConversationSession,
    ScheduledTrigger,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowSnapshot,
)


def minute_bucket(now: datetime) -> datetime:
    """Truncate a timestamp to minute precision.

    Beat's scheduler can drift by a few hundred milliseconds each tick,
    so two back-to-back fires that should collide on the same minute
    boundary need their ``scheduled_for`` values to match exactly. A
    minute-aligned timestamp makes the UNIQUE(workflow_def_id,
    scheduled_for) dedupe deterministic.
    """
    return now.replace(second=0, microsecond=0)

logger = logging.getLogger(__name__)

celery_app.conf.beat_schedule = {
    "check-scheduled-workflows": {
        "task": "orchestrator.check_scheduled_workflows",
        "schedule": 60.0,
    },
    "poll-async-jobs": {
        "task": "orchestrator.poll_async_jobs",
        # Tight cadence. Individual jobs opt into slower polling via their
        # metadata.poll_interval_seconds (stored in async_jobs.next_poll_at).
        "schedule": 30.0,
    },
    "prune-old-snapshots": {
        "task": "orchestrator.prune_old_snapshots",
        "schedule": crontab(hour=3, minute=0),  # daily at 3:00 AM
    },
    "prune-old-scheduled-triggers": {
        "task": "orchestrator.prune_old_scheduled_triggers",
        "schedule": crontab(hour=3, minute=30),  # daily at 3:30 AM
    },
    "archive-stale-conversation-episodes": {
        "task": "orchestrator.archive_stale_conversation_episodes",
        "schedule": 900.0,
    },
}


@celery_app.task(name="orchestrator.check_scheduled_workflows")
def check_scheduled_workflows():
    """Scan all workflows for schedule trigger nodes that are due.

    Dedupe is DB-enforced: for each due workflow we try to INSERT a
    ``scheduled_triggers`` row keyed by (workflow_def_id, minute-aligned
    scheduled_for). The UNIQUE constraint guarantees at most one fire
    per workflow per minute — whichever Beat tick wins the insert runs
    the workflow, everything else (clock skew, duplicate Beat, retry)
    raises IntegrityError and skips.
    """
    db = SessionLocal()
    try:
        # DV-07 — only active workflows fire on schedule. Manual Run
        # still works for inactive workflows (users toggle them off
        # precisely so cron stops while iteration continues).
        # COPILOT-01b.ii.b — ephemeral rows never fire schedules; they
        # exist solely to back a copilot trial run that already
        # completed (or is being awaited via get_execution_logs).
        workflows = (
            db.query(WorkflowDefinition)
            .filter(
                WorkflowDefinition.is_active.is_(True),
                WorkflowDefinition.is_ephemeral.is_(False),
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        scheduled_for = minute_bucket(now)
        triggered = 0

        for wf in workflows:
            cron_expr = _extract_schedule_cron(wf.graph_json)
            if not cron_expr:
                continue
            if not _is_due(cron_expr, now):
                continue

            # Atomic claim — unique constraint is the exactly-once latch.
            claim = ScheduledTrigger(
                workflow_def_id=wf.id,
                scheduled_for=scheduled_for,
            )
            db.add(claim)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                logger.debug(
                    "Schedule for workflow %s at %s already claimed — skipping",
                    wf.id, scheduled_for.isoformat(),
                )
                continue

            instance = WorkflowInstance(
                tenant_id=wf.tenant_id,
                workflow_def_id=wf.id,
                trigger_payload={
                    "source": "schedule",
                    "cron": cron_expr,
                    "fired_at": now.isoformat(),
                    "scheduled_for": scheduled_for.isoformat(),
                },
                status="queued",
                definition_version_at_start=wf.version,
            )
            db.add(instance)
            db.flush()

            claim.instance_id = instance.id
            db.commit()
            db.refresh(instance)

            from app.workers.tasks import execute_workflow_task
            execute_workflow_task.delay(wf.tenant_id, str(instance.id))
            triggered += 1
            logger.info(
                "Scheduled trigger fired for workflow %s (cron: %s, scheduled_for: %s)",
                wf.id, cron_expr, scheduled_for.isoformat(),
            )

        if triggered:
            logger.info("Schedule check complete: %d workflows triggered", triggered)
    except Exception:
        logger.exception("Error in scheduled workflow check")
        db.rollback()
    finally:
        db.close()


@celery_app.task(name="orchestrator.prune_old_scheduled_triggers")
def prune_old_scheduled_triggers():
    """Delete scheduled_triggers rows older than 24h.

    The dedupe check only looks at the current minute, so older rows
    have no functional value — we keep 24h for audit / debugging
    ("did this workflow actually fire at 03:00?") then drop.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    db = SessionLocal()
    try:
        deleted = (
            db.query(ScheduledTrigger)
            .filter(ScheduledTrigger.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Pruned %d scheduled_triggers rows older than 24h", deleted)
    except Exception:
        logger.exception("Error pruning old scheduled_triggers")
        db.rollback()
    finally:
        db.close()


@celery_app.task(name="orchestrator.poll_async_jobs")
def poll_async_jobs():
    """Poll external async systems for jobs that are past their
    next_poll_at deadline; resume parent workflows on terminal outcomes
    or timeout.

    Currently handles system='automationedge'. Adding another external
    system is one ``if system ==`` branch that delegates to a different
    client + terminal-status helper.

    Per-job cadence: ``next_poll_at`` is the deadline for the next poll.
    Jobs with a 5-minute poll interval get skipped for 10 Beat ticks;
    jobs with a 10-second interval get picked up every tick. Natural
    backoff + rate limiting without a separate scheduling table.
    """
    from datetime import datetime, timezone
    from app.engine.async_job_poller import (
        budget_from_metadata, check_timeout, compute_transition,
        next_poll_at,
    )
    from app.engine.async_job_finalizer import (
        finalize_terminal, finalize_timeout,
    )
    from app.engine.automationedge_client import (
        AEConnection, get_status, terminal_status_for,
    )

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        batch = (
            db.query(AsyncJob)
            .filter(
                AsyncJob.status.in_(("submitted", "running")),
                AsyncJob.next_poll_at <= now,
            )
            .order_by(AsyncJob.next_poll_at.asc())
            .limit(100)
            .all()
        )
        if not batch:
            return

        resumed = 0
        for job in batch:
            try:
                if job.system != "automationedge":
                    logger.warning(
                        "Unsupported async_job system %r (id=%s) — skipping",
                        job.system, job.id,
                    )
                    continue
                conn = _ae_conn_from_metadata(job)

                # Short-circuit: if the parent instance is already cancelled
                # or no longer suspended, drop the job.
                instance = (
                    db.query(WorkflowInstance)
                    .filter_by(id=job.instance_id)
                    .first()
                )
                if instance is None or instance.status != "suspended":
                    job.status = "abandoned"
                    db.commit()
                    continue

                # Pre-poll timeout check catches jobs that have drifted so far
                # past their budget we shouldn't even bother asking AE.
                budget = budget_from_metadata(job.metadata_json)
                verdict = check_timeout(
                    submitted_at=job.submitted_at,
                    total_diverted_ms=job.total_diverted_ms,
                    diverted_since=job.diverted_since,
                    budget=budget,
                    now=now,
                )
                if verdict.timed_out:
                    finalize_timeout(db, job, verdict.reason or "timeout", now)
                    resumed += 1
                    continue

                # Poll AE
                try:
                    ae_body = get_status(conn, job.external_job_id)
                except Exception as exc:
                    job.last_polled_at = now
                    job.next_poll_at = next_poll_at(budget, now)
                    job.last_error = str(exc)[:2000]
                    db.commit()
                    logger.warning(
                        "AE poll failed for job %s — will retry at %s",
                        job.id, job.next_poll_at,
                    )
                    continue

                ae_status = ae_body.get("status")
                transition = compute_transition(
                    prior_last_external_status=job.last_external_status,
                    prior_total_diverted_ms=job.total_diverted_ms,
                    prior_diverted_since=job.diverted_since,
                    new_ae_status=ae_status,
                    now=now,
                )
                job.last_external_status = transition.new_last_external_status
                job.total_diverted_ms = transition.new_total_diverted_ms
                job.diverted_since = transition.new_diverted_since
                job.last_polled_at = now

                terminal = terminal_status_for(ae_status)
                if terminal is None:
                    # Still running (or Diverted) — reschedule.
                    job.status = "running" if ae_status else job.status
                    job.next_poll_at = next_poll_at(budget, now)
                    db.commit()
                    continue

                # Terminal outcome — resume the parent.
                finalize_terminal(db, job, terminal, ae_body, now)
                resumed += 1

            except Exception:
                logger.exception("poll_async_jobs: job %s raised — continuing", job.id)
                db.rollback()

        if resumed:
            logger.info("poll_async_jobs: resumed %d parent workflow(s)", resumed)
    except Exception:
        logger.exception("poll_async_jobs top-level failure")
        db.rollback()
    finally:
        db.close()


def _ae_conn_from_metadata(job: "AsyncJob"):
    """Rebuild an AEConnection from the snapshot stored at submit time."""
    from app.engine.automationedge_client import AEConnection
    meta = job.metadata_json or {}
    instance_tenant_id = _resolve_tenant_for_job(job)
    return AEConnection(
        base_url=meta["base_url"],
        tenant_id=instance_tenant_id,
        credentials_secret_prefix=meta.get("credentials_secret_prefix", "AUTOMATIONEDGE"),
        auth_mode=meta.get("auth_mode", "ae_session"),
        org_code=meta.get("org_code"),
        source=meta.get("source", "AE AI Hub Orchestrator"),
        user_id=meta.get("user_id", "orchestrator"),
    )


def _resolve_tenant_for_job(job: "AsyncJob") -> str:
    """Join to workflow_instances to get the tenant that owns this job."""
    db = SessionLocal()
    try:
        inst = db.query(WorkflowInstance).filter_by(id=job.instance_id).first()
        if inst is None:
            raise RuntimeError(f"async_job {job.id} has no parent instance")
        return inst.tenant_id
    finally:
        db.close()


# _finalize_terminal and _finalize_timeout moved to
# app/engine/async_job_finalizer.py so the webhook endpoint can share
# the exact same resume path.


@celery_app.task(name="orchestrator.prune_old_snapshots")
def prune_old_snapshots():
    """Delete old workflow snapshots beyond the configured max_snapshots limit.

    Keeps the most recent N snapshots per workflow and deletes the rest.

    ADMIN-01: ``max_snapshots`` is looked up per-tenant via
    ``tenant_policy_resolver``. A tenant-level override = 0 means
    "unlimited" for that tenant's workflows. Policies are resolved
    once per tenant per run (not per workflow) to avoid redundant
    DB hits.
    """
    from app.engine.tenant_policy_resolver import get_effective_policy

    db = SessionLocal()
    try:
        # Join workflow_snapshots → workflow_definitions to pull the
        # tenant_id alongside each workflow_def_id. This is a daily
        # task, not a hot path — one join beats N tenant lookups.
        from sqlalchemy import distinct

        rows = (
            db.query(
                distinct(WorkflowSnapshot.workflow_def_id),
                WorkflowDefinition.tenant_id,
            )
            .join(
                WorkflowDefinition,
                WorkflowDefinition.id == WorkflowSnapshot.workflow_def_id,
            )
            .all()
        )

        # Resolve each tenant's policy once; reuse for every workflow
        # owned by that tenant.
        tenant_max_keep: dict[str, int] = {}
        total_pruned = 0
        for wf_id, tenant_id in rows:
            if tenant_id not in tenant_max_keep:
                tenant_max_keep[tenant_id] = get_effective_policy(tenant_id).max_snapshots
            max_keep = tenant_max_keep[tenant_id]
            if max_keep <= 0:
                continue  # 0 = unlimited for this tenant

            snapshots = (
                db.query(WorkflowSnapshot)
                .filter_by(workflow_def_id=wf_id)
                .order_by(WorkflowSnapshot.version.desc())
                .all()
            )

            if len(snapshots) <= max_keep:
                continue

            to_delete = snapshots[max_keep:]
            for snap in to_delete:
                db.delete(snap)
            total_pruned += len(to_delete)

        if total_pruned:
            db.commit()
            logger.info(
                "Pruned %d old snapshots across %d workflows (per-tenant policies)",
                total_pruned, len(rows),
            )
    except Exception:
        logger.exception("Error in snapshot pruning")
        db.rollback()
    finally:
        db.close()


@celery_app.task(name="orchestrator.archive_stale_conversation_episodes")
def archive_stale_conversation_episodes():
    """Archive inactive active episodes so working memory stays issue-scoped.

    Candidates are collected per-tenant (up to 50 each) so no single tenant
    can monopolise the global 500-row archival window.
    """
    _PER_TENANT_LIMIT = 50
    _GLOBAL_LIMIT = 500

    db = SessionLocal()
    try:
        # Collect distinct tenants that have active episodes.
        tenant_ids = [
            row[0]
            for row in db.query(ConversationEpisode.tenant_id)
            .filter(ConversationEpisode.status == "active")
            .distinct()
            .all()
        ]

        candidate_ids: list = []
        for tenant_id in tenant_ids:
            if len(candidate_ids) >= _GLOBAL_LIMIT:
                break
            rows = (
                db.query(ConversationEpisode.id)
                .filter(
                    ConversationEpisode.tenant_id == tenant_id,
                    ConversationEpisode.status == "active",
                )
                .order_by(ConversationEpisode.last_activity_at.asc())
                .limit(_PER_TENANT_LIMIT)
                .all()
            )
            candidate_ids.extend(row[0] for row in rows)
    except Exception:
        logger.exception("Error listing stale conversation episode candidates")
        return
    finally:
        db.close()

    archived_count = 0
    for episode_id in candidate_ids:
        item_db = SessionLocal()
        try:
            episode = (
                item_db.query(ConversationEpisode)
                .filter_by(id=episode_id, status="active")
                .first()
            )
            if episode is None:
                continue

            session = (
                item_db.query(ConversationSession)
                .filter_by(id=episode.session_ref_id, tenant_id=episode.tenant_id)
                .with_for_update()
                .first()
            )
            if session is None:
                continue

            policy = resolve_episode_policy(
                item_db,
                tenant_id=episode.tenant_id,
                episode=episode,
            )
            if policy.episode_inactivity_minutes <= 0 or episode.last_activity_at is None:
                continue
            idle_seconds = (datetime.now(timezone.utc) - episode.last_activity_at).total_seconds()
            if idle_seconds < policy.episode_inactivity_minutes * 60:
                continue

            archived_episode, _ = archive_active_episode(
                item_db,
                tenant_id=episode.tenant_id,
                session=session,
                workflow_def_id=str(episode.workflow_def_id) if episode.workflow_def_id else None,
                instance_id=None,
                node_id="scheduler_archive_episode",
                context={},
                reason="inactive",
            )
            item_db.commit()
            if archived_episode and archived_episode.status == "archived":
                archived_count += 1
        except Exception:
            item_db.rollback()
            logger.exception("Error archiving stale conversation episode %s", episode_id)
        finally:
            item_db.close()

    if archived_count:
        logger.info("Archived %d stale conversation episodes", archived_count)


def _extract_schedule_cron(graph_json: dict) -> str | None:
    """Find a Schedule Trigger node and return its cron expression."""
    for node in graph_json.get("nodes", []):
        data = node.get("data", {})
        if data.get("label") == "Schedule Trigger" and data.get("nodeCategory") == "trigger":
            return data.get("config", {}).get("cron")
    return None


def _is_due(cron_expr: str, now: datetime) -> bool:
    """Check if a cron expression is due within the last 60 seconds."""
    try:
        cron = croniter(cron_expr, now)
        prev = cron.get_prev(datetime)
        return (now - prev).total_seconds() <= 60
    except (ValueError, KeyError):
        logger.warning("Invalid cron expression: %s", cron_expr)
        return False

