"""ADMIN-01 — tests for ``engine/tenant_policy_resolver``.

The resolver must:

* Fall back to env defaults when tenant_id is None, no row exists,
  or the DB lookup raises (the last one is production-safety: a
  broken tenant_policies read mustn't 500 every /execute).
* Honour per-field null so a row with only one override still
  picks up the env default for the other fields.
* Return accurate ``source`` metadata so the admin UI can show
  "tenant_policy" vs. "env_default" per field.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def patched_settings():
    """Freeze env defaults for the duration of a test."""
    from app.config import settings

    with patch.object(settings, "execution_quota_per_hour", 50), \
         patch.object(settings, "max_snapshots", 20), \
         patch.object(settings, "mcp_pool_size", 4), \
         patch.object(settings, "rate_limit_requests", 100), \
         patch.object(settings, "rate_limit_window_seconds", 60):
        yield settings


def _row(
    execution_quota_per_hour: int | None = None,
    max_snapshots: int | None = None,
    mcp_pool_size: int | None = None,
    rate_limit_requests_per_window: int | None = None,
    rate_limit_window_seconds: int | None = None,
    smart_04_lints_enabled: bool | None = None,
    smart_06_mcp_discovery_enabled: bool | None = None,
    smart_02_pattern_library_enabled: bool | None = None,
    smart_01_scenario_memory_enabled: bool | None = None,
    smart_01_strict_promote_gate_enabled: bool | None = None,
):
    row = MagicMock()
    row.execution_quota_per_hour = execution_quota_per_hour
    row.max_snapshots = max_snapshots
    row.mcp_pool_size = mcp_pool_size
    row.rate_limit_requests_per_window = rate_limit_requests_per_window
    row.rate_limit_window_seconds = rate_limit_window_seconds
    # SMART-XX flags — None means "column exists on the row but no
    # value was set for the override", matching nullable-column
    # semantics (resolver should treat it as env fallback).
    row.smart_04_lints_enabled = smart_04_lints_enabled
    row.smart_06_mcp_discovery_enabled = smart_06_mcp_discovery_enabled
    row.smart_02_pattern_library_enabled = smart_02_pattern_library_enabled
    row.smart_01_scenario_memory_enabled = smart_01_scenario_memory_enabled
    row.smart_01_strict_promote_gate_enabled = smart_01_strict_promote_gate_enabled
    return row


def _session_returning(row):
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = row
    return session


class TestEnvFallback:
    def test_no_tenant_returns_env_defaults_with_source(self, patched_settings):
        from app.engine.tenant_policy_resolver import get_effective_policy

        policy = get_effective_policy(None)

        assert policy.execution_quota_per_hour == 50
        assert policy.max_snapshots == 20
        assert policy.mcp_pool_size == 4
        assert policy.rate_limit_requests_per_window == 100
        assert policy.rate_limit_window_seconds == 60
        assert policy.source == {
            "execution_quota_per_hour": "env_default",
            "max_snapshots": "env_default",
            "mcp_pool_size": "env_default",
            "rate_limit_requests_per_window": "env_default",
            "rate_limit_window_seconds": "env_default",
            # SMART-XX flags (every SMART-XX ticket that ships adds
            # its key here).
            "smart_04_lints_enabled": "env_default",
            "smart_06_mcp_discovery_enabled": "env_default",
            "smart_02_pattern_library_enabled": "env_default",
            "smart_01_scenario_memory_enabled": "env_default",
            "smart_01_strict_promote_gate_enabled": "env_default",
        }

    def test_no_row_for_tenant_returns_env_defaults(self, patched_settings):
        from app.engine.tenant_policy_resolver import get_effective_policy

        with patch("app.database.SessionLocal", return_value=_session_returning(None)), \
             patch("app.database.set_tenant_context"):
            policy = get_effective_policy("tenant-a")

        assert policy.execution_quota_per_hour == 50
        assert policy.source["execution_quota_per_hour"] == "env_default"

    def test_db_error_falls_back_to_env_not_raise(self, patched_settings):
        """Prod safety: a broken DB must NOT 500 every quota check."""
        from app.engine.tenant_policy_resolver import get_effective_policy

        bad_session_factory = MagicMock(side_effect=RuntimeError("db down"))

        with patch("app.database.SessionLocal", bad_session_factory):
            policy = get_effective_policy("tenant-a")

        # Graceful degrade — env defaults with env_default sources.
        assert policy.execution_quota_per_hour == 50
        assert policy.source["execution_quota_per_hour"] == "env_default"


class TestRowOverrides:
    def test_full_override_row_used(self, patched_settings):
        from app.engine.tenant_policy_resolver import get_effective_policy

        row = _row(
            execution_quota_per_hour=200,
            max_snapshots=5,
            mcp_pool_size=8,
            rate_limit_requests_per_window=500,
            rate_limit_window_seconds=30,
        )
        with patch("app.database.SessionLocal", return_value=_session_returning(row)), \
             patch("app.database.set_tenant_context"):
            policy = get_effective_policy("tenant-a")

        assert policy.execution_quota_per_hour == 200
        assert policy.max_snapshots == 5
        assert policy.mcp_pool_size == 8
        assert policy.rate_limit_requests_per_window == 500
        assert policy.rate_limit_window_seconds == 30
        assert policy.source == {
            "execution_quota_per_hour": "tenant_policy",
            "max_snapshots": "tenant_policy",
            "mcp_pool_size": "tenant_policy",
            "rate_limit_requests_per_window": "tenant_policy",
            "rate_limit_window_seconds": "tenant_policy",
            # SMART-XX flags — row fixture doesn't set them so they
            # fall through to env_default.
            "smart_04_lints_enabled": "env_default",
            "smart_06_mcp_discovery_enabled": "env_default",
            "smart_02_pattern_library_enabled": "env_default",
            "smart_01_scenario_memory_enabled": "env_default",
            "smart_01_strict_promote_gate_enabled": "env_default",
        }

    def test_partial_override_inherits_missing_fields_from_env(self, patched_settings):
        """The whole point of nullable columns — a tenant who only
        cares about one knob doesn't have to re-state the others."""
        from app.engine.tenant_policy_resolver import get_effective_policy

        row = _row(execution_quota_per_hour=500)  # others stay null
        with patch("app.database.SessionLocal", return_value=_session_returning(row)), \
             patch("app.database.set_tenant_context"):
            policy = get_effective_policy("tenant-a")

        assert policy.execution_quota_per_hour == 500
        assert policy.source["execution_quota_per_hour"] == "tenant_policy"

        # Other fields fall through to env with env_default source.
        assert policy.max_snapshots == 20
        assert policy.source["max_snapshots"] == "env_default"
        assert policy.mcp_pool_size == 4
        assert policy.source["mcp_pool_size"] == "env_default"


class TestIntegrationWithCallSites:
    """Sanity-checks that the resolver's contract matches what the
    three call sites expect — plain ints for the knobs, ``source``
    dict for the admin UI, no surprises."""

    def test_resolver_output_shape_is_plain_ints(self, patched_settings):
        from app.engine.tenant_policy_resolver import get_effective_policy

        policy = get_effective_policy(None)

        # Call sites do bare arithmetic / comparisons — no Pydantic,
        # no SQLAlchemy instances, just ints + str keys.
        assert isinstance(policy.execution_quota_per_hour, int)
        assert isinstance(policy.max_snapshots, int)
        assert isinstance(policy.mcp_pool_size, int)
        for k, v in policy.source.items():
            assert isinstance(k, str)
            assert v in ("tenant_policy", "env_default")
