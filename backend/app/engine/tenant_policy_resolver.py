"""ADMIN-01 — resolve a tenant's effective policy values.

Read-path for every operational knob that migrated from
``settings`` onto the per-tenant ``tenant_policies`` table. The
precedence is the same shape as MCP-02 / VERTEX-02:

  1. ``tenant_policies`` column for this tenant, if non-null
  2. ``settings.<knob>`` env default

Returns a frozen dataclass with both the effective value AND a
per-field source label so the admin UI can show operators what's
overridden vs. what's defaulting to env. Callers that need the plain
int use ``policy.execution_quota_per_hour`` and friends.

No caching — policy changes should take effect immediately from the
admin UI's perspective. The read is one indexed lookup per call, and
every call site is already low-frequency (quota check once per
execute, pool construction once per (tenant, server), snapshot prune
once per day).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import settings

logger = logging.getLogger(__name__)

PolicySource = Literal["tenant_policy", "env_default"]


@dataclass(frozen=True)
class EffectivePolicy:
    """The resolved, ready-to-use policy for a tenant.

    ``source`` names where each field actually came from so admin
    tooling can show "this one is overridden, this one is inherited".
    """

    execution_quota_per_hour: int
    max_snapshots: int
    mcp_pool_size: int
    # ADMIN-02
    rate_limit_requests_per_window: int
    rate_limit_window_seconds: int
    # SMART-04 — toggle the copilot's proactive authoring lints.
    # Resolver returns bool; the runner-tool layer reads it and skips
    # lint computation when off (still runs schema validation).
    smart_04_lints_enabled: bool
    # SMART-06 — toggle the copilot's MCP tool discovery path.
    # When off, the ``discover_mcp_tools`` runner tool returns an
    # empty list with ``discovery_enabled: false``.
    smart_06_mcp_discovery_enabled: bool
    # SMART-02 — toggle the accepted-patterns library. Off = promote
    # doesn't persist a pattern row and ``recall_patterns`` returns
    # an empty list.
    smart_02_pattern_library_enabled: bool
    # SMART-01 — scenario memory (auto-save execute_draft as test
    # scenarios) + strict promote-gate (refuse promote on any
    # failing scenario). BOTH default off per tenant unless
    # explicitly enabled.
    smart_01_scenario_memory_enabled: bool
    smart_01_strict_promote_gate_enabled: bool
    # SMART-05 — vector-backed docs search for the copilot. Default
    # off; word-overlap search is the fallback and the automatic
    # degrade path when the embedding provider is unreachable.
    smart_05_vector_docs_enabled: bool
    source: dict[str, PolicySource]


def _env_defaults() -> EffectivePolicy:
    return EffectivePolicy(
        execution_quota_per_hour=settings.execution_quota_per_hour,
        max_snapshots=settings.max_snapshots,
        mcp_pool_size=settings.mcp_pool_size,
        rate_limit_requests_per_window=settings.rate_limit_requests,
        rate_limit_window_seconds=settings.rate_limit_window_seconds,
        smart_04_lints_enabled=settings.smart_04_lints_enabled,
        smart_06_mcp_discovery_enabled=settings.smart_06_mcp_discovery_enabled,
        smart_02_pattern_library_enabled=settings.smart_02_pattern_library_enabled,
        smart_01_scenario_memory_enabled=settings.smart_01_scenario_memory_enabled,
        smart_01_strict_promote_gate_enabled=settings.smart_01_strict_promote_gate_enabled,
        smart_05_vector_docs_enabled=settings.smart_05_vector_docs_enabled,
        source={
            "execution_quota_per_hour": "env_default",
            "max_snapshots": "env_default",
            "mcp_pool_size": "env_default",
            "rate_limit_requests_per_window": "env_default",
            "rate_limit_window_seconds": "env_default",
            "smart_04_lints_enabled": "env_default",
            "smart_06_mcp_discovery_enabled": "env_default",
            "smart_02_pattern_library_enabled": "env_default",
            "smart_01_scenario_memory_enabled": "env_default",
            "smart_01_strict_promote_gate_enabled": "env_default",
            "smart_05_vector_docs_enabled": "env_default",
        },
    )


def get_effective_policy(tenant_id: str | None) -> EffectivePolicy:
    """Resolve the effective policy for a tenant.

    ``tenant_id=None`` is the cross-tenant / internal path — used by
    Beat tasks that iterate every workflow regardless of tenant.
    Those callers get the env defaults.
    """
    if tenant_id is None:
        return _env_defaults()

    # Short-lived session + RLS scope; same pattern as MCP / Vertex
    # resolvers. Keep the import local so unit tests can patch
    # ``app.database.SessionLocal`` without circular-import gymnastics.
    from app.database import SessionLocal, set_tenant_context
    from app.models.workflow import TenantPolicy

    db = None
    try:
        db = SessionLocal()
        set_tenant_context(db, tenant_id)
        row = db.query(TenantPolicy).filter_by(tenant_id=tenant_id).first()
        if row is None:
            return _env_defaults()

        # Per-field resolution: a null column means "fall through to
        # the env default". Record the source so the UI can show
        # which fields are overridden.
        source: dict[str, PolicySource] = {}

        def _pick(col_value: int | None, env_value: int, field: str) -> int:
            if col_value is None:
                source[field] = "env_default"
                return env_value
            source[field] = "tenant_policy"
            return col_value

        def _pick_bool(col_value: bool | None, env_value: bool, field: str) -> bool:
            """Boolean variant — SMART-xx feature flags. Null on the
            row means "inherit env default", which also happens for
            rows created before the column was added."""
            if col_value is None:
                source[field] = "env_default"
                return env_value
            source[field] = "tenant_policy"
            return col_value

        return EffectivePolicy(
            execution_quota_per_hour=_pick(
                row.execution_quota_per_hour,
                settings.execution_quota_per_hour,
                "execution_quota_per_hour",
            ),
            max_snapshots=_pick(
                row.max_snapshots,
                settings.max_snapshots,
                "max_snapshots",
            ),
            mcp_pool_size=_pick(
                row.mcp_pool_size,
                settings.mcp_pool_size,
                "mcp_pool_size",
            ),
            rate_limit_requests_per_window=_pick(
                row.rate_limit_requests_per_window,
                settings.rate_limit_requests,
                "rate_limit_requests_per_window",
            ),
            rate_limit_window_seconds=_pick(
                row.rate_limit_window_seconds,
                settings.rate_limit_window_seconds,
                "rate_limit_window_seconds",
            ),
            smart_04_lints_enabled=_pick_bool(
                getattr(row, "smart_04_lints_enabled", None),
                settings.smart_04_lints_enabled,
                "smart_04_lints_enabled",
            ),
            smart_06_mcp_discovery_enabled=_pick_bool(
                getattr(row, "smart_06_mcp_discovery_enabled", None),
                settings.smart_06_mcp_discovery_enabled,
                "smart_06_mcp_discovery_enabled",
            ),
            smart_02_pattern_library_enabled=_pick_bool(
                getattr(row, "smart_02_pattern_library_enabled", None),
                settings.smart_02_pattern_library_enabled,
                "smart_02_pattern_library_enabled",
            ),
            smart_01_scenario_memory_enabled=_pick_bool(
                getattr(row, "smart_01_scenario_memory_enabled", None),
                settings.smart_01_scenario_memory_enabled,
                "smart_01_scenario_memory_enabled",
            ),
            smart_01_strict_promote_gate_enabled=_pick_bool(
                getattr(row, "smart_01_strict_promote_gate_enabled", None),
                settings.smart_01_strict_promote_gate_enabled,
                "smart_01_strict_promote_gate_enabled",
            ),
            smart_05_vector_docs_enabled=_pick_bool(
                getattr(row, "smart_05_vector_docs_enabled", None),
                settings.smart_05_vector_docs_enabled,
                "smart_05_vector_docs_enabled",
            ),
            source=source,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        # Any failure (connection refused, missing table, RLS denial)
        # must NOT hard-fail the caller. Quota enforcement is the
        # hot-path use — degrading to env defaults is far safer than
        # 500-ing every execute because the tenant_policies table is
        # unreachable for some reason. Log and continue.
        logger.warning(
            "tenant_policy_resolver: failed to read tenant %r policy, "
            "falling back to env defaults (%s)",
            tenant_id, exc,
        )
        return _env_defaults()
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
