"""AutomationEdge REST client (v5.4+).

Wraps the three endpoints we need for an async-execution node:

  * POST /rest/authenticate         — exchanges username+password for a
                                      session token (form-urlencoded)
  * POST /rest/execute              — submits a workflow, returns
                                      automationRequestId (our
                                      external_job_id)
  * GET  /rest/workflowinstances/{id} — polls for status + response

Two auth modes:

  * ``ae_session`` (default) — the stock AE auth model. Login once,
    cache the session token in memory keyed by (base_url, username),
    send ``X-session-token`` on every subsequent call, re-login on 401.
  * ``bearer`` — ``Authorization: Bearer <token>`` for deployments
    fronting AE with an API gateway or a future AE build that exposes
    API keys.

The session cache is process-local and thread-safe. For Celery worker
processes each worker has its own cache; a cold-start login costs
~100 ms per worker per (base_url, username), which is fine.

The AE ``workflowResponse`` field is a JSON-encoded **string** — the
caller is responsible for running ``json.loads`` on it if they want
structured access to the embedded output parameters.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.security.vault import get_tenant_secret

logger = logging.getLogger(__name__)

AuthMode = Literal["ae_session", "bearer"]


# Terminal mappings — AE's native status strings → our async_jobs.status.
# ``Diverted`` is intentionally NOT listed here; it's a "held for human"
# pause state, not terminal. The Beat poller keeps polling Diverted jobs
# with the pause-the-clock timeout accounting.
_AE_TERMINAL_STATUS: dict[str, str] = {
    "Complete":   "completed",
    "Success":    "completed",   # alias seen in some AE builds
    "Failure":    "failed",
    "Failed":     "failed",
    "Terminated": "cancelled",
}


def terminal_status_for(ae_status: str | None) -> str | None:
    """Map an AE status string to our async_jobs.status, or None if the
    job is still in-flight (including Diverted)."""
    if ae_status is None:
        return None
    return _AE_TERMINAL_STATUS.get(ae_status)


@dataclass
class AEConnection:
    """Resolved connection info for one AE call.

    Produced by the integration-config resolver (per-node config overlaid
    on tenant_integrations defaults). The client treats this as an
    opaque bundle — it doesn't care whether the fields came from the
    node or the tenant integration.
    """

    base_url: str                 # e.g. http://localhost:8080/aeengine/rest
    tenant_id: str                # our orchestrator tenant, for vault lookups
    credentials_secret_prefix: str  # vault key prefix, e.g. "AUTOMATIONEDGE"
    auth_mode: AuthMode = "ae_session"
    org_code: str | None = None   # AE tenant orgCode (for the /execute body)
    source: str = "AE AI Hub Orchestrator"
    user_id: str = "orchestrator"

    def api_url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


# ---------------------------------------------------------------------------
# Session-token cache (for auth_mode='ae_session')
# ---------------------------------------------------------------------------

# Keyed by (base_url, username). Value is the session token string.
_session_cache: dict[tuple[str, str], str] = {}
_session_cache_lock = threading.Lock()


def _cache_get(base_url: str, username: str) -> str | None:
    with _session_cache_lock:
        return _session_cache.get((base_url, username))


def _cache_put(base_url: str, username: str, token: str) -> None:
    with _session_cache_lock:
        _session_cache[(base_url, username)] = token


def _cache_invalidate(base_url: str, username: str) -> None:
    with _session_cache_lock:
        _session_cache.pop((base_url, username), None)


def reset_session_cache() -> None:
    """Test hook — clear the whole in-memory session cache."""
    with _session_cache_lock:
        _session_cache.clear()


# ---------------------------------------------------------------------------
# Auth + low-level HTTP
# ---------------------------------------------------------------------------

class AEAuthError(Exception):
    """Raised when AE rejects our credentials or the session token."""


def _resolve_credentials(conn: AEConnection) -> dict[str, str]:
    """Load the right credentials from the vault based on auth_mode."""
    prefix = conn.credentials_secret_prefix
    if conn.auth_mode == "bearer":
        token = get_tenant_secret(conn.tenant_id, f"{prefix}_TOKEN")
        if not token:
            raise AEAuthError(
                f"Missing vault secret {prefix}_TOKEN for tenant {conn.tenant_id}"
            )
        return {"token": token}
    # ae_session
    username = get_tenant_secret(conn.tenant_id, f"{prefix}_USERNAME")
    password = get_tenant_secret(conn.tenant_id, f"{prefix}_PASSWORD")
    if not username or not password:
        raise AEAuthError(
            f"Missing vault secrets {prefix}_USERNAME/{prefix}_PASSWORD "
            f"for tenant {conn.tenant_id}"
        )
    return {"username": username, "password": password}


def _login(conn: AEConnection, creds: dict[str, str]) -> str:
    """POST /rest/authenticate and return the session token."""
    url = conn.api_url("authenticate")
    response = httpx.post(
        url,
        data={"username": creds["username"], "password": creds["password"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10.0,
    )
    if response.status_code != 200:
        raise AEAuthError(
            f"AE /authenticate returned {response.status_code}: {response.text[:500]}"
        )
    body = response.json()
    if not body.get("success"):
        raise AEAuthError(
            f"AE /authenticate reported success=false: {body.get('message')}"
        )
    token = body.get("sessionToken")
    if not token:
        raise AEAuthError("AE /authenticate response missing sessionToken")
    return token


def _auth_headers(conn: AEConnection, creds: dict[str, str]) -> dict[str, str]:
    """Produce the headers for a non-login call, using cached session or
    bearer token."""
    if conn.auth_mode == "bearer":
        return {"Authorization": f"Bearer {creds['token']}"}

    # ae_session — look up or acquire a session token
    token = _cache_get(conn.base_url, creds["username"])
    if token is None:
        token = _login(conn, creds)
        _cache_put(conn.base_url, creds["username"], token)
    return {"X-session-token": token}


def _request_with_retry(
    conn: AEConnection,
    method: str,
    path: str,
    *,
    json_body: Any = None,
    timeout: float = 20.0,
) -> httpx.Response:
    """Perform a request, refreshing the session token on 401 once."""
    creds = _resolve_credentials(conn)
    headers = _auth_headers(conn, creds)
    headers["Content-Type"] = "application/json"

    url = conn.api_url(path)
    response = httpx.request(
        method, url, headers=headers, json=json_body, timeout=timeout,
    )

    if response.status_code == 401 and conn.auth_mode == "ae_session":
        # Session expired — invalidate and retry exactly once.
        logger.info("AE session expired for %s; re-logging in", creds["username"])
        _cache_invalidate(conn.base_url, creds["username"])
        headers = _auth_headers(conn, creds)
        headers["Content-Type"] = "application/json"
        response = httpx.request(
            method, url, headers=headers, json=json_body, timeout=timeout,
        )

    return response


# ---------------------------------------------------------------------------
# Public client operations
# ---------------------------------------------------------------------------

def submit_workflow(
    conn: AEConnection,
    *,
    workflow_name: str,
    params: list[dict[str, Any]],
    source_id: str | None = None,
    response_mail_subject: str | None = None,
    input_attributes: dict[str, Any] | None = None,
) -> int:
    """POST /rest/execute. Returns the ``automationRequestId`` integer."""
    body: dict[str, Any] = {
        "orgCode": conn.org_code,
        "workflowName": workflow_name,
        "userId": conn.user_id,
        "source": conn.source,
        "sourceId": source_id or "",
        "responseMailSubject": response_mail_subject,
        "params": params,
    }
    # AE accepts up to 6 generic attribute slots; pass through any the
    # caller provided so downstream AE reports can include them.
    for i in range(1, 7):
        body[f"inputAttribute{i}"] = (input_attributes or {}).get(f"inputAttribute{i}")

    response = _request_with_retry(conn, "POST", "execute", json_body=body, timeout=20.0)
    if response.status_code != 200:
        raise RuntimeError(
            f"AE /execute returned {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"AE /execute success=false: {data.get('errorDetails')}")
    req_id = data.get("automationRequestId")
    if req_id is None:
        raise RuntimeError("AE /execute response missing automationRequestId")
    return int(req_id)


def get_status(conn: AEConnection, automation_request_id: int | str) -> dict[str, Any]:
    """GET /rest/workflowinstances/{id}. Returns the parsed body with
    ``workflow_response_parsed`` helper added when possible."""
    response = _request_with_retry(
        conn, "GET", f"workflowinstances/{automation_request_id}", timeout=15.0,
    )
    if response.status_code == 404:
        raise RuntimeError(
            f"AE workflowinstance {automation_request_id} not found "
            "(deleted? wrong base URL?)"
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"AE /workflowinstances/{automation_request_id} returned "
            f"{response.status_code}: {response.text[:500]}"
        )
    data = response.json()

    # AE wraps the per-workflow output as a JSON-encoded STRING inside
    # the .workflowResponse field. Parse it opportunistically so the
    # caller can reach the inner output parameters without another JSON
    # dance — leave the raw string in place for consumers that want it.
    raw = data.get("workflowResponse")
    if isinstance(raw, str) and raw.strip():
        try:
            data["workflow_response_parsed"] = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug(
                "AE workflowResponse for %s is not valid JSON; skipping parse",
                automation_request_id,
            )
    return data


def try_terminate(
    conn: AEConnection,
    automation_request_id: int | str,
) -> bool:
    """Best-effort termination.

    AE 5.4 does not document a dedicated terminate endpoint; newer
    versions may support ``PUT /rest/workflowinstances/{id}/terminate``.
    We try it and swallow 404 — callers get a bool so they can log the
    outcome but not fail cancellation on it.
    """
    try:
        response = _request_with_retry(
            conn, "PUT",
            f"workflowinstances/{automation_request_id}/terminate",
            timeout=10.0,
        )
    except Exception as exc:
        logger.warning(
            "AE terminate call for %s failed transport-level: %s",
            automation_request_id, exc,
        )
        return False
    if response.status_code in (200, 202, 204):
        return True
    logger.info(
        "AE terminate for %s returned %s — treating as unsupported on this AE build",
        automation_request_id, response.status_code,
    )
    return False
