"""Unit tests for the pure helpers driving the async_jobs Beat poller.

Covers:
  * Diverted-pause transition logic (enter / exit / stay / legacy repair)
  * Two-budget timeout arithmetic (active runtime + max diverted span)
  * Resume context-patch shaping
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.async_job_poller import (
    AsyncJobBudget,
    AE_DIVERTED,
    budget_from_metadata,
    build_resume_context_patch,
    check_timeout,
    compute_transition,
    next_poll_at,
)

UTC = timezone.utc


def _t(h=0, m=0, s=0):
    return datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC) + timedelta(hours=h, minutes=m, seconds=s)


class TestBudgetFromMetadata:
    def test_defaults_when_metadata_empty(self):
        b = budget_from_metadata(None)
        assert b.timeout_seconds == 3600
        assert b.max_diverted_seconds == 604800
        assert b.poll_interval_seconds == 30

    def test_reads_from_metadata(self):
        b = budget_from_metadata({
            "timeout_seconds": 120,
            "max_diverted_seconds": 86400,
            "poll_interval_seconds": 10,
        })
        assert b == AsyncJobBudget(120, 86400, 10)

    def test_clamps_poll_interval_to_minimum_5s(self):
        b = budget_from_metadata({"poll_interval_seconds": 1})
        assert b.poll_interval_seconds == 5


class TestComputeTransition:
    def test_entering_diverted_sets_diverted_since(self):
        out = compute_transition(
            prior_last_external_status="Executing",
            prior_total_diverted_ms=0,
            prior_diverted_since=None,
            new_ae_status=AE_DIVERTED,
            now=_t(s=10),
        )
        assert out.new_diverted_since == _t(s=10)
        assert out.new_total_diverted_ms == 0
        assert out.new_last_external_status == AE_DIVERTED

    def test_exiting_diverted_banks_the_elapsed_span(self):
        # 30s in Diverted
        out = compute_transition(
            prior_last_external_status=AE_DIVERTED,
            prior_total_diverted_ms=0,
            prior_diverted_since=_t(s=10),
            new_ae_status="Executing",
            now=_t(s=40),
        )
        assert out.new_diverted_since is None
        assert out.new_total_diverted_ms == 30_000
        assert out.new_last_external_status == "Executing"

    def test_exiting_accumulates_on_top_of_prior_bank(self):
        out = compute_transition(
            prior_last_external_status=AE_DIVERTED,
            prior_total_diverted_ms=1_000,        # 1s from a previous span
            prior_diverted_since=_t(s=10),
            new_ae_status="Complete",
            now=_t(s=15),
        )
        assert out.new_total_diverted_ms == 6_000  # 1s + 5s

    def test_staying_in_diverted_preserves_since(self):
        out = compute_transition(
            prior_last_external_status=AE_DIVERTED,
            prior_total_diverted_ms=500,
            prior_diverted_since=_t(s=10),
            new_ae_status=AE_DIVERTED,
            now=_t(s=100),
        )
        assert out.new_diverted_since == _t(s=10)   # unchanged
        assert out.new_total_diverted_ms == 500     # not banked while ongoing

    def test_staying_non_diverted_preserves_bank(self):
        out = compute_transition(
            prior_last_external_status="Executing",
            prior_total_diverted_ms=42_000,
            prior_diverted_since=None,
            new_ae_status="Executing",
            now=_t(s=200),
        )
        assert out.new_total_diverted_ms == 42_000
        assert out.new_diverted_since is None

    def test_legacy_diverted_without_anchor_gets_repaired(self):
        # Row says last_external_status='Diverted' but diverted_since is
        # NULL — pre-0017 data or operator intervention. Re-anchor on now.
        out = compute_transition(
            prior_last_external_status=AE_DIVERTED,
            prior_total_diverted_ms=0,
            prior_diverted_since=None,
            new_ae_status=AE_DIVERTED,
            now=_t(s=10),
        )
        assert out.new_diverted_since == _t(s=10)

    def test_negative_elapsed_does_not_bank(self):
        # Clock skew: diverted_since is in the future vs now. Should not
        # decrement the bank.
        out = compute_transition(
            prior_last_external_status=AE_DIVERTED,
            prior_total_diverted_ms=10_000,
            prior_diverted_since=_t(s=50),
            new_ae_status="Executing",
            now=_t(s=40),
        )
        assert out.new_total_diverted_ms == 10_000
        assert out.new_diverted_since is None


class TestCheckTimeout:
    BUDGET_SMALL = AsyncJobBudget(
        timeout_seconds=60, max_diverted_seconds=120, poll_interval_seconds=10,
    )

    def test_not_timed_out_under_active_budget(self):
        v = check_timeout(
            submitted_at=_t(),
            total_diverted_ms=0,
            diverted_since=None,
            budget=self.BUDGET_SMALL,
            now=_t(s=30),
        )
        assert v.timed_out is False
        assert v.reason is None

    def test_timed_out_when_active_exceeds_budget(self):
        v = check_timeout(
            submitted_at=_t(),
            total_diverted_ms=0,
            diverted_since=None,
            budget=self.BUDGET_SMALL,
            now=_t(s=61),
        )
        assert v.timed_out is True
        assert "active runtime exceeded 60s" in v.reason

    def test_diverted_time_does_not_count_against_active_budget(self):
        # 30s active + 90s in Diverted = 120s wall. Active = 30s → fine.
        v = check_timeout(
            submitted_at=_t(),
            total_diverted_ms=90_000,
            diverted_since=None,
            budget=self.BUDGET_SMALL,
            now=_t(s=120),
        )
        assert v.timed_out is False

    def test_current_divert_span_also_excluded(self):
        # 50s active, still in Diverted for another 80s. Active = 50s → fine
        # because current open Diverted span is also excluded.
        v = check_timeout(
            submitted_at=_t(),
            total_diverted_ms=0,
            diverted_since=_t(s=50),
            budget=self.BUDGET_SMALL,
            now=_t(s=130),
        )
        assert v.timed_out is False

    def test_divert_span_hits_max_diverted_cap(self):
        v = check_timeout(
            submitted_at=_t(),
            total_diverted_ms=0,
            diverted_since=_t(s=0),
            budget=self.BUDGET_SMALL,
            now=_t(s=125),     # 125s in current Diverted span, cap is 120s
        )
        assert v.timed_out is True
        assert "Diverted state for more than 120s" in v.reason

    def test_banked_total_divert_is_not_what_caps(self):
        # Only the *current* span is checked against max_diverted — banked
        # spans from completed divert cycles don't re-trigger the cap.
        v = check_timeout(
            submitted_at=_t(),
            total_diverted_ms=500_000,   # way over max_diverted
            diverted_since=None,
            budget=self.BUDGET_SMALL,
            now=_t(s=40),
        )
        assert v.timed_out is False


class TestNextPollAt:
    def test_returns_now_plus_interval(self):
        b = AsyncJobBudget(3600, 604800, 45)
        now = _t()
        assert next_poll_at(b, now) == now + timedelta(seconds=45)


class TestBuildResumeContextPatch:
    def test_completed_with_parsed_output_parameters(self):
        ae = {
            "id": 42,
            "status": "Complete",
            "workflow_response_parsed": {
                "message": "Execution Successful",
                "outputParameters": {"result": 7},
            },
        }
        patch = build_resume_context_patch(
            node_id="node_3",
            outcome="completed",
            ae_response=ae,
            external_job_id="42",
        )
        body = patch["node_3"]
        assert body["state"] == "completed"
        assert body["external_job_id"] == "42"
        assert body["output_parameters"] == {"result": 7}
        assert body["ae_message"] == "Execution Successful"
        assert body["ae_response"] is ae

    def test_failure_carries_failure_reason(self):
        patch = build_resume_context_patch(
            node_id="node_3",
            outcome="failed",
            ae_response={
                "status": "Failure",
                "failureReason": "NetworkError",
                "failureReasonDescription": "timeout talking to SAP",
            },
            error="AE reported Failure",
        )
        body = patch["node_3"]
        assert body["state"] == "failed"
        assert body["error"] == "AE reported Failure"
        assert body["failureReason"] == "NetworkError"
        assert body["failureReasonDescription"] == "timeout talking to SAP"

    def test_timeout_surfaces_warning_flag(self):
        patch = build_resume_context_patch(
            node_id="node_3",
            outcome="timed_out",
            ae_response=None,
            timeout_reason="AE job active runtime exceeded 60s",
        )
        body = patch["node_3"]
        assert body["state"] == "timed_out"
        assert body["_warning"] is True
        assert "exceeded 60s" in body["error"]

    def test_cancelled_shape(self):
        patch = build_resume_context_patch(
            node_id="node_3",
            outcome="cancelled",
            ae_response=None,
            error="Parent workflow cancelled",
        )
        assert patch["node_3"]["state"] == "cancelled"
        assert patch["node_3"]["error"] == "Parent workflow cancelled"
