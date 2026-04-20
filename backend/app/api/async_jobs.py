"""Async-job webhook endpoint.

``POST /api/v1/async-jobs/{job_id}/complete`` lets an external system
(AutomationEdge, future Jenkins/Temporal) report terminal status for
a job that our orchestrator is currently suspended on. It's the
Pattern A opt-in that complements the Pattern C Beat poller — both
funnel through the same ``finalize_terminal`` helper so UI and context
shape are identical regardless of which path fired.

This endpoint is **not** gated by the usual tenant JWT middleware —
the caller is the external system, which doesn't have our JWTs.
Authentication is instead one of (stored at submission time in
``async_jobs.metadata_json``):

  * ``token`` mode — caller supplies ``?token=<secret>`` in the query
    string, compared against ``metadata.webhook_token`` with
    ``hmac.compare_digest``.
  * ``hmac`` mode — caller sends ``X-AE-Signature: sha256=<hex>`` where
    the hex is HMAC-SHA256 of the raw request body keyed by
    ``metadata.webhook_hmac_secret``. Protects against URL leakage and
    replay of bodies.
  * ``both`` — either mechanism is accepted. Useful during migration
    or when the AE workflow author can only implement one of the two.

The endpoint deliberately returns minimal information (``{"ok": true}``
or a generic 4xx) so a misconfigured callback can't be used as an
oracle to probe which jobs exist.
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from hashlib import sha256

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.engine.async_job_finalizer import finalize_terminal
from app.engine.automationedge_client import terminal_status_for
from app.models.workflow import AsyncJob

router = APIRouter(prefix="/api/v1/async-jobs", tags=["async-jobs"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signature helpers — pure, unit-tested independently of the endpoint
# ---------------------------------------------------------------------------

def verify_token(provided: str | None, expected: str | None) -> bool:
    """Constant-time compare for the token-in-query-param mode.

    Returns False for any None / empty-string case rather than short-
    circuiting so callers can rely on a single boolean answer.
    """
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def compute_hmac_sha256(secret: str, body: bytes) -> str:
    """Return hex digest of HMAC-SHA256(secret, body)."""
    return hmac.new(secret.encode(), body, sha256).hexdigest()


def verify_hmac(
    signature_header: str | None,
    body: bytes,
    expected_secret: str | None,
) -> bool:
    """Constant-time compare of the provided ``X-AE-Signature`` against
    the freshly-computed HMAC over the request body.

    Accepts both ``sha256=<hex>`` and a bare ``<hex>`` form — some HTTP
    client libraries strip the algorithm prefix.
    """
    if not signature_header or not expected_secret or not body:
        return False
    raw = signature_header.strip()
    if raw.lower().startswith("sha256="):
        raw = raw.split("=", 1)[1]
    expected = compute_hmac_sha256(expected_secret, body)
    return hmac.compare_digest(raw.encode(), expected.encode())


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _authenticate(
    job: AsyncJob,
    *,
    token: str | None,
    signature: str | None,
    body_bytes: bytes,
) -> None:
    """Validate the caller against the job's configured webhook auth mode.

    Raises HTTPException(401) with a generic message on any failure —
    we never reveal which check failed to avoid handing timing / oracle
    information to attackers.
    """
    meta = job.metadata_json or {}
    mode = meta.get("webhook_auth", "token")

    token_ok = False
    hmac_ok = False
    if mode in ("token", "both"):
        token_ok = verify_token(token, meta.get("webhook_token"))
    if mode in ("hmac", "both"):
        hmac_ok = verify_hmac(signature, body_bytes, meta.get("webhook_hmac_secret"))

    if mode == "token" and not token_ok:
        raise HTTPException(status_code=401, detail="invalid webhook credentials")
    if mode == "hmac" and not hmac_ok:
        raise HTTPException(status_code=401, detail="invalid webhook credentials")
    if mode == "both" and not (token_ok or hmac_ok):
        raise HTTPException(status_code=401, detail="invalid webhook credentials")


@router.post("/{job_id}/complete")
async def complete_async_job(
    job_id: uuid.UUID,
    request: Request,
    token: str | None = Query(default=None),
    x_ae_signature: str | None = Header(default=None, alias="X-AE-Signature"),
    body: dict = Body(default_factory=dict),
) -> dict:
    """Mark an async_jobs row terminal based on an external-system callback.

    Body shape is the same AE ``workflowinstances/{id}`` payload the
    Beat poller consumes: at minimum ``{"status": "Complete" | "Failure"
    | "Terminated"}``. Extra fields (``workflowResponse``,
    ``failureReason``, etc.) flow into the resume context_patch.
    """
    # Read raw bytes once so HMAC verification uses exactly what the caller sent.
    body_bytes = await request.body()

    db: Session = SessionLocal()
    try:
        job = db.query(AsyncJob).filter_by(id=job_id).first()
        if job is None:
            # Mirror the 401 timing so presence of the row isn't observable.
            raise HTTPException(status_code=401, detail="invalid webhook credentials")

        if job.status not in ("submitted", "running"):
            # Idempotent — re-delivered webhook for an already-finalised job.
            return {"ok": True, "status": job.status, "note": "already finalised"}

        _authenticate(
            job,
            token=token,
            signature=x_ae_signature,
            body_bytes=body_bytes,
        )

        ae_status = body.get("status") if isinstance(body, dict) else None
        terminal = terminal_status_for(ae_status)
        if terminal is None:
            # Non-terminal status on a webhook call means the AE workflow
            # author misconfigured their callback step (e.g. fired before
            # the process was done). Leave the job alone and let Beat
            # keep polling.
            logger.info(
                "Webhook received non-terminal status %r for async_job %s — ignoring",
                ae_status, job.id,
            )
            return {"ok": True, "note": "non-terminal status ignored"}

        normalised = body if isinstance(body, dict) else {}
        # Mirror the Beat poller's get_status behaviour: AE emits
        # ``workflowResponse`` as a JSON-encoded string, and the context
        # patch builder looks for ``workflow_response_parsed`` to promote
        # ``outputParameters`` onto the top level of the resume payload.
        # Parse it opportunistically so downstream context shape is
        # identical whether completion arrived via poll or webhook.
        raw_wf_response = normalised.get("workflowResponse")
        if isinstance(raw_wf_response, str) and raw_wf_response.strip():
            try:
                normalised["workflow_response_parsed"] = json.loads(raw_wf_response)
            except json.JSONDecodeError:
                logger.debug(
                    "Webhook workflowResponse for async_job %s is not valid JSON; skipping parse",
                    job.id,
                )

        finalize_terminal(db, job, terminal, normalised)
        return {"ok": True, "status": terminal}
    finally:
        db.close()
