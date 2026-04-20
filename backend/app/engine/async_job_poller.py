"""Pure helpers driving the async_jobs Beat poller.

Split out of ``app/workers/scheduler.py`` so the Diverted-pause logic
and the timeout arithmetic can be unit-tested without Celery, without
a live DB, and without respx. The scheduler task just orchestrates
SessionLocal + iteration and delegates every decision to the helpers
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

# AE's "held for human intervention" state. Kept as a constant so the
# pause-the-clock logic doesn't sprinkle the string literal through the
# codebase — if a future external system has its own name for this
# state, the set of "human-pause" states becomes per-system.
AE_DIVERTED = "Diverted"


@dataclass(frozen=True)
class AsyncJobBudget:
    """Timing budget extracted from async_job.metadata_json.

    Two independent budgets — one for active runtime, one for total
    time in the Diverted state. Neither counts against the other.
    """
    timeout_seconds: int
    max_diverted_seconds: int
    poll_interval_seconds: int


def budget_from_metadata(metadata: dict[str, Any] | None) -> AsyncJobBudget:
    meta = metadata or {}
    return AsyncJobBudget(
        timeout_seconds=int(meta.get("timeout_seconds", 3600)),
        max_diverted_seconds=int(meta.get("max_diverted_seconds", 604800)),
        poll_interval_seconds=max(int(meta.get("poll_interval_seconds", 30)), 5),
    )


@dataclass
class TransitionUpdate:
    """Mutations to apply to an AsyncJob row when its external status
    changes. Returned by ``compute_transition`` so callers (the Beat
    task and the webhook endpoint) apply the same state machine.
    """
    new_last_external_status: str | None
    new_total_diverted_ms: int
    new_diverted_since: datetime | None


def compute_transition(
    *,
    prior_last_external_status: str | None,
    prior_total_diverted_ms: int,
    prior_diverted_since: datetime | None,
    new_ae_status: str | None,
    now: datetime,
) -> TransitionUpdate:
    """Derive the next Diverted-tracking state from an observed AE status.

    Rules:
      * Entering Diverted (prior != Diverted, new == Diverted): set
        diverted_since = now.
      * Exiting Diverted (prior == Diverted, new != Diverted): bank the
        elapsed span into total_diverted_ms; clear diverted_since.
      * Staying in the same bucket: preserve state.
      * prior_diverted_since = None while the job is in Diverted is
        allowed (legacy rows) — treated as entering Diverted for
        accounting purposes.
    """
    prior_is_diverted = prior_last_external_status == AE_DIVERTED
    new_is_diverted = new_ae_status == AE_DIVERTED

    total = prior_total_diverted_ms
    since = prior_diverted_since

    if prior_is_diverted and not new_is_diverted:
        # Exiting — bank the span (may be zero if prior_diverted_since missing).
        if prior_diverted_since is not None:
            elapsed_ms = int((now - prior_diverted_since).total_seconds() * 1000)
            if elapsed_ms > 0:
                total = total + elapsed_ms
        since = None
    elif not prior_is_diverted and new_is_diverted:
        since = now
    elif prior_is_diverted and new_is_diverted and prior_diverted_since is None:
        # Repair a legacy row that says we're Diverted but has no anchor.
        since = now

    return TransitionUpdate(
        new_last_external_status=new_ae_status,
        new_total_diverted_ms=total,
        new_diverted_since=since,
    )


@dataclass(frozen=True)
class TimeoutVerdict:
    """Outcome of a timeout check. ``reason`` is None when the job is
    within both budgets. When set, it's surfaced to the user in the
    resume payload as ``error``."""
    timed_out: bool
    reason: str | None


def check_timeout(
    *,
    submitted_at: datetime,
    total_diverted_ms: int,
    diverted_since: datetime | None,
    budget: AsyncJobBudget,
    now: datetime,
) -> TimeoutVerdict:
    """Apply the two-budget timeout model.

    Active runtime budget counts wall-clock time minus every span the
    job has spent in Diverted (banked spans in ``total_diverted_ms``
    plus the current open span since ``diverted_since``).
    """
    total_elapsed_ms = int((now - submitted_at).total_seconds() * 1000)
    current_divert_ms = (
        int((now - diverted_since).total_seconds() * 1000)
        if diverted_since is not None else 0
    )
    active_ms = total_elapsed_ms - total_diverted_ms - current_divert_ms
    # Clamp because wall-clock skew could produce a negative reading.
    active_ms = max(active_ms, 0)

    if active_ms > budget.timeout_seconds * 1000:
        return TimeoutVerdict(
            True,
            f"AE job active runtime exceeded {budget.timeout_seconds}s "
            "(Diverted time excluded from the budget)",
        )
    if current_divert_ms > budget.max_diverted_seconds * 1000:
        return TimeoutVerdict(
            True,
            f"AE job held in Diverted state for more than "
            f"{budget.max_diverted_seconds}s",
        )
    return TimeoutVerdict(False, None)


def next_poll_at(budget: AsyncJobBudget, now: datetime) -> datetime:
    return now + timedelta(seconds=budget.poll_interval_seconds)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Node-output shaping
# ---------------------------------------------------------------------------

NodeOutcome = Literal["completed", "failed", "cancelled", "timed_out"]


def build_resume_context_patch(
    *,
    node_id: str,
    outcome: NodeOutcome,
    ae_response: dict[str, Any] | None,
    error: str | None = None,
    external_job_id: str | None = None,
    timeout_reason: str | None = None,
) -> dict[str, Any]:
    """Shape the dict that gets merged into the parent workflow context
    via ``resume_graph``'s context_patch.

    Downstream Condition nodes branch on keys like ``node_X.state`` or
    ``node_X._warning`` to drive different paths. The AE response is
    preserved in full under ``node_X.ae_response`` for richer
    introspection.
    """
    patch_body: dict[str, Any] = {
        "state": outcome,
    }
    if external_job_id is not None:
        patch_body["external_job_id"] = external_job_id
    if ae_response is not None:
        patch_body["ae_response"] = ae_response
        # Promote common AE fields to top level for easier access.
        if isinstance(ae_response, dict):
            if "workflow_response_parsed" in ae_response:
                parsed = ae_response["workflow_response_parsed"]
                if isinstance(parsed, dict):
                    patch_body["output_parameters"] = parsed.get("outputParameters")
                    patch_body["ae_message"] = parsed.get("message")
            for k in ("failureReason", "failureReasonDescription"):
                if k in ae_response and ae_response[k]:
                    patch_body[k] = ae_response[k]
    if error is not None:
        patch_body["error"] = error
    if timeout_reason is not None:
        patch_body["error"] = timeout_reason
        patch_body["_warning"] = True
    return {node_id: patch_body}
