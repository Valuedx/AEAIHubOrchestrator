"""Server-Sent Events endpoint for real-time execution updates.

Clients connect to GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/stream
and receive a stream of JSON events as the workflow executes:

  event: log
  data: {"node_id": "node_2", "status": "running", ...}

  event: status
  data: {"instance_status": "completed"}

  event: token          ← NEW (V0.9.8 Rich Streaming)
  data: {"node_id": "node_2", "token": "The ", "done": false}

  event: done
  data: {"instance_status": "completed"}

Token events are sourced from a Redis pub/sub channel
``orch:stream:{instance_id}`` that the Celery LLM workers publish to
during streaming LLM calls.  If Redis is unavailable the token stream is
silently skipped — log/status/done events continue uninterrupted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, set_tenant_context
from app.security.tenant import get_tenant_id
from app.models.workflow import WorkflowInstance, ExecutionLog

router = APIRouter(prefix="/api/v1/workflows", tags=["sse"])
logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "orch:stream"


def _serialize_dt(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


async def _subscribe_tokens(instance_id: str, queue: asyncio.Queue) -> None:
    """Subscribe to the Redis token channel and push messages to the queue.

    Runs as an asyncio task alongside the DB-poll loop.  Exits when the
    Redis connection is closed or an error occurs — the outer loop continues
    without tokens.
    """
    try:
        from redis.asyncio import Redis as AsyncRedis
        from app.config import settings

        channel = f"{_CHANNEL_PREFIX}:{instance_id}"
        async with AsyncRedis.from_url(settings.redis_url, decode_responses=True) as r:
            async with r.pubsub() as ps:
                await ps.subscribe(channel)
                async for message in ps.listen():
                    if message["type"] == "message":
                        try:
                            data = json.loads(message["data"])
                            await queue.put(data)
                        except (json.JSONDecodeError, Exception):
                            pass
    except Exception as exc:
        logger.debug("Redis token subscription ended (non-fatal): %s", exc)


@router.get("/{workflow_id}/instances/{instance_id}/stream")
async def stream_instance(
    workflow_id: str,
    instance_id: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
):
    """Stream execution updates as Server-Sent Events.

    Emits three event types:
    - ``log``    — fired each time a new ExecutionLog row is available
    - ``status`` — fired when the instance status changes
    - ``token``  — fired for each streaming LLM token (when supported)
    - ``done``   — fired once when the instance reaches a terminal status
    """
    db = SessionLocal()
    set_tenant_context(db, tenant_id)
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    db.close()

    if not instance:
        raise HTTPException(404, "Instance not found")

    async def event_generator():
        last_log_count = 0
        last_status = None
        token_queue: asyncio.Queue = asyncio.Queue()

        # Start the Redis subscriber task in the background
        redis_task = asyncio.create_task(_subscribe_tokens(instance_id, token_queue))

        try:
            while True:
                if await request.is_disconnected():
                    break

                # ── Drain the token queue (non-blocking) ────────────────────
                while not token_queue.empty():
                    try:
                        token_msg = token_queue.get_nowait()
                        yield (
                            f"event: token\n"
                            f"data: {json.dumps(token_msg)}\n\n"
                        )
                    except asyncio.QueueEmpty:
                        break

                # ── DB poll for log and status events ───────────────────────
                db = SessionLocal()
                try:
                    set_tenant_context(db, tenant_id)
                    inst = db.query(WorkflowInstance).filter_by(id=instance_id).first()
                    if not inst:
                        break

                    logs = (
                        db.query(ExecutionLog)
                        .filter_by(instance_id=instance_id)
                        .order_by(ExecutionLog.started_at)
                        .all()
                    )

                    for log in logs[last_log_count:]:
                        log_data = {
                            "id": str(log.id),
                            "node_id": log.node_id,
                            "node_type": log.node_type,
                            "status": log.status,
                            "started_at": log.started_at,
                            "completed_at": log.completed_at,
                            "error": log.error,
                        }
                        yield f"event: log\ndata: {json.dumps(log_data, default=_serialize_dt)}\n\n"

                    last_log_count = len(logs)

                    if inst.status != last_status:
                        last_status = inst.status
                        status_data = {
                            "instance_status": inst.status,
                            "current_node_id": inst.current_node_id,
                        }
                        yield f"event: status\ndata: {json.dumps(status_data)}\n\n"

                    if inst.status in (
                        "completed",
                        "failed",
                        "suspended",
                        "cancelled",
                        "paused",
                    ):
                        final = {
                            "instance_status": inst.status,
                            "completed_at": inst.completed_at,
                        }
                        yield f"event: done\ndata: {json.dumps(final, default=_serialize_dt)}\n\n"
                        break

                finally:
                    db.close()

                await asyncio.sleep(1.0)

        finally:
            redis_task.cancel()
            try:
                await redis_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
