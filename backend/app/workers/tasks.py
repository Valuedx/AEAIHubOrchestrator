"""Workflow execution tasks.

Supports two modes controlled by ``settings.use_celery``:

* **Celery mode** (``use_celery=True``): tasks are dispatched via Celery
  and require a running Redis broker + Celery worker.
* **In-process mode** (``use_celery=False``, default for local dev): tasks
  run immediately in a background thread — no external dependencies needed.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


def _run_in_thread(fn, *args: Any, **kwargs: Any) -> None:
    """Run *fn* in a daemon thread with its own DB session."""

    def _wrapper():
        db = SessionLocal()
        try:
            fn(db, *args, **kwargs)
        except Exception:
            logger.exception("Background task %s failed", fn.__name__)
        finally:
            db.close()

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Public task helpers — the API layer calls these; they transparently choose
# Celery or in-process depending on settings.use_celery.
# ---------------------------------------------------------------------------

class _TaskProxy:
    """Mimics Celery task's ``.delay()`` interface so callers don't change."""

    def __init__(self, fn, celery_task_name: str):
        self._fn = fn
        self._celery_task_name = celery_task_name

    def delay(self, *args: Any, **kwargs: Any):
        if settings.use_celery:
            from app.workers.celery_app import celery_app
            return celery_app.send_task(self._celery_task_name, args=args, kwargs=kwargs)
        logger.info("Running %s in-process (use_celery=False)", self._celery_task_name)
        self._fn(*args, **kwargs)


# -- actual implementations (accept positional args matching Celery signatures)

def _execute_workflow(instance_id: str, deterministic_mode: bool = False):
    from app.engine.dag_runner import execute_graph
    _run_in_thread(execute_graph, instance_id, deterministic_mode=deterministic_mode)


def _resume_workflow(instance_id: str, approval_payload: dict | None = None, context_patch: dict | None = None):
    from app.engine.dag_runner import resume_graph
    _run_in_thread(resume_graph, instance_id, approval_payload or {}, context_patch=context_patch)


def _retry_workflow(instance_id: str, from_node_id: str | None = None):
    from app.engine.dag_runner import retry_graph
    _run_in_thread(retry_graph, instance_id, from_node_id)


def _resume_paused_workflow(instance_id: str, context_patch: dict | None = None):
    from app.engine.dag_runner import resume_paused_graph
    _run_in_thread(resume_paused_graph, instance_id, context_patch=context_patch)


# -- public task objects (drop-in replacements — callers use .delay())
execute_workflow_task = _TaskProxy(_execute_workflow, "orchestrator.execute_workflow")
resume_workflow_task = _TaskProxy(_resume_workflow, "orchestrator.resume_workflow")
retry_workflow_task = _TaskProxy(_retry_workflow, "orchestrator.retry_workflow")
resume_paused_workflow_task = _TaskProxy(_resume_paused_workflow, "orchestrator.resume_paused_workflow")
