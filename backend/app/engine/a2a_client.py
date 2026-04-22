"""Outbound A2A (Agent-to-Agent) client.

Used by the A2A Agent Call node handler to delegate tasks to external
A2A-compatible agents.  Implements the Google A2A protocol v0.2 over
JSON-RPC 2.0 / HTTP.

Three public functions cover the full lifecycle:

    fetch_agent_card  — discover what skills a remote agent offers
    send_task         — submit a new task and get back a Task object
    poll_until_done   — poll tasks/get until the task reaches a terminal state

All functions raise on HTTP errors or protocol violations so the node
handler can surface a clean error in the execution log.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# A2A terminal states — polling stops when the task reaches any of these
_TERMINAL_STATES = {"completed", "failed", "canceled", "input-required"}

# Default poll interval in seconds
_POLL_INTERVAL = 3.0


def fetch_agent_card(agent_card_url: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET the remote agent's /.well-known/agent.json discovery document.

    Returns the parsed agent card dict.  Raises httpx.HTTPStatusError if
    the remote server returns a non-2xx response.
    """
    logger.info("A2A: fetching agent card from %s", agent_card_url)
    resp = httpx.get(agent_card_url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    card = resp.json()
    logger.info(
        "A2A: agent card received name=%r skills=%d",
        card.get("name"), len(card.get("skills", [])),
    )
    return card


def send_task(
    agent_url: str,
    skill_id: str,
    message: str,
    api_key: str,
    session_id: str | None = None,
    task_id: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Submit a new task to a remote A2A agent (tasks/send).

    Returns the Task object from the JSON-RPC result.

    Args:
        agent_url:  Base URL of the remote agent (from agent card's ``url`` field).
        skill_id:   ID of the skill to invoke (workflow_def_id on orchestrator agents).
        message:    Plain-text message to send as the task input.
        api_key:    Bearer token for authenticating with the remote agent.
        session_id: Optional conversation thread ID for multi-turn context.
        task_id:    Optional caller-supplied idempotency key.
        timeout:    HTTP request timeout in seconds.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tasks/send",
        "params": {
            "skillId": skill_id,
            "sessionId": session_id or str(uuid.uuid4()),
            "message": {
                "role": "user",
                "parts": [{"text": message}],
            },
        },
    }
    if task_id:
        payload["params"]["id"] = task_id

    logger.info("A2A: sending task skill=%s to %s", skill_id, agent_url)
    resp = httpx.post(
        agent_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    resp.raise_for_status()

    body = resp.json()
    if "error" in body:
        raise RuntimeError(
            f"A2A tasks/send error {body['error'].get('code')}: "
            f"{body['error'].get('message')}"
        )

    task = body["result"]
    logger.info("A2A: task created id=%s state=%s", task["id"], task["status"]["state"])
    return task


def poll_until_done(
    agent_url: str,
    task_id: str,
    api_key: str,
    timeout_seconds: int = 300,
    poll_interval: float = _POLL_INTERVAL,
) -> dict[str, Any]:
    """Poll tasks/get until the task reaches a terminal state.

    Returns the final Task object.  Raises TimeoutError if the task does
    not complete within ``timeout_seconds``.

    Note: ``input-required`` is treated as terminal here — the caller
    (node handler) decides what to do with a paused remote task.
    """
    deadline = time.monotonic() + timeout_seconds
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        payload = {
            "jsonrpc": "2.0",
            "id": attempt,
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        try:
            resp = httpx.post(
                agent_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("A2A: poll attempt %d failed (%s), retrying…", attempt, exc)
            time.sleep(poll_interval)
            continue

        if "error" in body:
            raise RuntimeError(
                f"A2A tasks/get error {body['error'].get('code')}: "
                f"{body['error'].get('message')}"
            )

        task = body["result"]
        state = task["status"]["state"]
        logger.debug("A2A: poll attempt=%d task=%s state=%s", attempt, task_id, state)

        if state in _TERMINAL_STATES:
            logger.info("A2A: task %s reached terminal state=%s", task_id, state)
            return task

        time.sleep(poll_interval)

    raise TimeoutError(
        f"A2A task {task_id} did not reach a terminal state within {timeout_seconds}s"
    )


def extract_response_parts(task: dict[str, Any]) -> dict[str, Any]:
    """Fan out a completed task's artifacts into ``{text, data, files}``.

    A2A-01.c: remote agents can return mixed TextPart / DataPart /
    FilePart content. The Agent Call node previously only saw text;
    this helper returns the full surface so downstream nodes can
    route on the structured payload. The ``file`` variant's bytes
    field is base64-decoded into raw bytes for the caller — URIs
    pass through as-is.

    Shape::

        {
          "text":  "concatenated prose",
          "data":  [JSON-any, JSON-any, ...],   # one per DataPart
          "files": [                            # one per FilePart
            {"name": str|None, "mimeType": str|None,
             "bytes": bytes|None, "uri": str|None},
            ...
          ],
        }

    Falls back to the status message's parts when the task carried
    no artifacts (simple agents inline the answer in ``status.message``).
    """
    import base64

    text_bits: list[str] = []
    data_bits: list[Any] = []
    file_bits: list[dict[str, Any]] = []

    def _absorb_parts(source_parts: Any) -> None:
        if not isinstance(source_parts, list):
            return
        for part in source_parts:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                text_bits.append(part["text"])
            if part.get("data") is not None:
                data_bits.append(part["data"])
            file_ref = part.get("file")
            if isinstance(file_ref, dict):
                raw = file_ref.get("bytes")
                decoded: bytes | None = None
                if isinstance(raw, str) and raw:
                    try:
                        decoded = base64.b64decode(raw)
                    except Exception:  # noqa: BLE001 — malformed input
                        logger.info(
                            "A2A: FilePart.bytes failed base64 decode "
                            "(name=%r) — passing through as raw string",
                            file_ref.get("name"),
                        )
                file_bits.append({
                    "name": file_ref.get("name"),
                    "mimeType": file_ref.get("mimeType") or part.get("mimeType"),
                    "bytes": decoded if decoded is not None else raw,
                    "uri": file_ref.get("uri"),
                })

    for artifact in task.get("artifacts", []):
        _absorb_parts(artifact.get("parts"))

    if not text_bits and not data_bits and not file_bits:
        # Fallback: status message text (some agents return answers
        # inline in status.message for simple tasks).
        status_msg = task.get("status", {}).get("message")
        if isinstance(status_msg, dict):
            _absorb_parts(status_msg.get("parts"))

    return {
        "text": "".join(text_bits),
        "data": data_bits,
        "files": file_bits,
    }


def extract_response_text(task: dict[str, Any]) -> str:
    """Back-compat shim — returns just the concatenated text from
    ``extract_response_parts``. Existing callers (A2A Agent Call
    node handler) keep working without a signature change.
    """
    return extract_response_parts(task)["text"]
