"""Finalisation path shared between the async-jobs Beat poller and the
``/async-jobs/{id}/complete`` webhook endpoint.

Both entry points converge on the same two-step flow:

  1. Mark the ``async_jobs`` row terminal (``completed`` / ``failed``
     / ``cancelled`` / ``timed_out``).
  2. Build a shaped ``context_patch`` from the AE response and dispatch
     ``resume_workflow_task`` so the parent workflow re-enters the
     ready queue exactly as it does for HITL resumes.

Keeping the logic in one module means the UI behaviour, logs, and
context shape are identical whether the completion came from a Beat
poll or a webhook — which is the whole point of offering both.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.engine.async_job_poller import build_resume_context_patch
from app.models.workflow import AsyncJob, WorkflowInstance

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def finalize_terminal(
    db: Session,
    job: AsyncJob,
    terminal: str,
    ae_body: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Mark ``job`` terminal and queue the parent workflow to resume.

    ``terminal`` is one of ``completed``, ``failed``, ``cancelled`` —
    matches the ``async_jobs.status`` enum. Other callers
    (``finalize_timeout``) handle the ``timed_out`` path separately.
    """
    now = now or _utcnow()
    job.status = terminal
    job.completed_at = now
    job.next_poll_at = now  # harmless; row is terminal and the poll query filters it out

    outcome_map = {
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }
    outcome = outcome_map.get(terminal, "failed")
    patch = build_resume_context_patch(
        node_id=job.node_id,
        outcome=outcome,
        ae_response=ae_body,
        external_job_id=str(job.external_job_id),
        error=ae_body.get("failureReason") if outcome == "failed" else None,
    )
    db.commit()

    instance = db.query(WorkflowInstance).filter_by(id=job.instance_id).first()
    if instance is None:
        logger.warning(
            "async_job %s terminal (%s) but parent instance missing — skipping resume",
            job.id, terminal,
        )
        return

    # Late import: workers.tasks pulls in celery_app which is heavy.
    from app.workers.tasks import resume_workflow_task
    resume_workflow_task.delay(
        instance.tenant_id,
        str(instance.id),
        {},        # approval_payload — unused for async-external resumes
        patch,     # context_patch
    )
    logger.info(
        "async_job %s terminal (%s); queued resume for instance %s",
        job.id, terminal, instance.id,
    )


def finalize_timeout(
    db: Session,
    job: AsyncJob,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Mark ``job`` timed_out and queue the parent to resume as failed
    with the warning flag set on the context patch."""
    now = now or _utcnow()
    job.status = "timed_out"
    job.completed_at = now
    job.next_poll_at = now
    job.last_error = reason

    patch = build_resume_context_patch(
        node_id=job.node_id,
        outcome="timed_out",
        ae_response=None,
        external_job_id=str(job.external_job_id),
        timeout_reason=reason,
    )
    db.commit()

    instance = db.query(WorkflowInstance).filter_by(id=job.instance_id).first()
    if instance is None:
        return

    from app.workers.tasks import resume_workflow_task
    resume_workflow_task.delay(
        instance.tenant_id,
        str(instance.id),
        {},
        patch,
    )
    logger.warning(
        "async_job %s timed out (%s); queued failed resume for instance %s",
        job.id, reason, instance.id,
    )
