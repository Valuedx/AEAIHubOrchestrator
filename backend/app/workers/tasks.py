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


# -- document ingestion --

def _ingest_document(document_id: str, file_bytes_b64: str):
    import base64
    from app.engine.ingestor import ingest_document
    from app.models.knowledge import KBDocument, KnowledgeBase

    db = SessionLocal()
    try:
        doc = db.query(KBDocument).filter_by(id=document_id).first()
        if not doc:
            logger.error("ingest_document: document %s not found", document_id)
            return

        kb = db.query(KnowledgeBase).filter_by(id=doc.kb_id).first()
        if not kb:
            logger.error("ingest_document: KB %s not found for doc %s", doc.kb_id, document_id)
            doc.status = "failed"
            doc.error = "Knowledge base not found"
            db.commit()
            return

        doc.status = "processing"
        db.commit()

        file_bytes = base64.b64decode(file_bytes_b64)
        chunk_count = ingest_document(
            db=db,
            document_id=doc.id,
            file_bytes=file_bytes,
            content_type=doc.content_type,
            kb=kb,
        )

        doc.chunk_count = chunk_count
        doc.status = "ready"
        db.flush()
        kb.document_count = (
            db.query(KBDocument)
            .filter_by(kb_id=kb.id, status="ready")
            .count()
        )
        db.commit()
        logger.info("ingest_document: doc=%s chunks=%d", document_id, chunk_count)

    except Exception as exc:
        logger.exception("ingest_document failed for doc=%s", document_id)
        try:
            db.rollback()
            doc_retry = db.query(KBDocument).filter_by(id=document_id).first()
            if doc_retry:
                doc_retry.status = "failed"
                doc_retry.error = str(exc)[:2000]
                db.commit()
        except Exception:
            logger.exception("Failed to mark document %s as failed", document_id)
    finally:
        db.close()


# -- public task objects (drop-in replacements — callers use .delay())
execute_workflow_task = _TaskProxy(_execute_workflow, "orchestrator.execute_workflow")
resume_workflow_task = _TaskProxy(_resume_workflow, "orchestrator.resume_workflow")
retry_workflow_task = _TaskProxy(_retry_workflow, "orchestrator.retry_workflow")
resume_paused_workflow_task = _TaskProxy(_resume_paused_workflow, "orchestrator.resume_paused_workflow")
ingest_document_task = _TaskProxy(_ingest_document, "orchestrator.ingest_document")
