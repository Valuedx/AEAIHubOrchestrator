"""Unit tests for the integration-config resolver used by the AE node
(and any future async-system node).

Precedence: per-node config > tenant_integrations row referenced by
``integrationLabel`` > ``is_default=true`` row for (tenant, system).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.engine.integration_resolver import (
    IntegrationConfigError,
    resolve_integration_config,
)


class _FakeQuery:
    """Enough SQLAlchemy surface to unit-test the resolver without a DB."""

    def __init__(self, rows):
        self._rows = rows
        self._filters: dict = {}

    def filter_by(self, **kwargs):
        self._filters.update(kwargs)
        return self

    def first(self):
        for row in self._rows:
            if all(getattr(row, k, None) == v for k, v in self._filters.items()):
                return row
        return None


def _make_db(rows):
    db = MagicMock()
    db.query.return_value = _FakeQuery(rows)
    return db


def _integration(label, config, is_default=False, tenant_id="t1", system="automationedge"):
    row = MagicMock()
    row.label = label
    row.tenant_id = tenant_id
    row.system = system
    row.is_default = is_default
    row.config_json = config
    return row


class TestResolveIntegrationConfig:
    def test_node_only_no_integration(self):
        db = _make_db([])
        merged = resolve_integration_config(
            db,
            tenant_id="t1",
            system="automationedge",
            node_config={"baseUrl": "http://ae/rest", "orgCode": "X", "workflowName": "wf"},
            required_fields=("baseUrl", "orgCode", "workflowName"),
        )
        assert merged["baseUrl"] == "http://ae/rest"

    def test_uses_is_default_integration_when_node_has_no_label(self):
        defaults = _integration(
            "prod-ae",
            {"baseUrl": "http://ae/rest", "orgCode": "PROD", "authMode": "ae_session"},
            is_default=True,
        )
        db = _make_db([defaults])
        merged = resolve_integration_config(
            db,
            tenant_id="t1",
            system="automationedge",
            node_config={"workflowName": "wf"},
            required_fields=("baseUrl", "orgCode", "workflowName"),
        )
        assert merged["baseUrl"] == "http://ae/rest"
        assert merged["orgCode"] == "PROD"
        assert merged["workflowName"] == "wf"

    def test_named_label_overrides_default(self):
        default_row = _integration(
            "default",
            {"baseUrl": "http://prod/rest", "orgCode": "PROD"},
            is_default=True,
        )
        dev_row = _integration(
            "dev-ae",
            {"baseUrl": "http://dev/rest", "orgCode": "DEV"},
        )
        db = _make_db([default_row, dev_row])
        merged = resolve_integration_config(
            db,
            tenant_id="t1",
            system="automationedge",
            node_config={"integrationLabel": "dev-ae", "workflowName": "wf"},
            required_fields=("baseUrl", "orgCode"),
        )
        assert merged["baseUrl"] == "http://dev/rest"
        assert merged["orgCode"] == "DEV"

    def test_node_fields_override_integration_fields(self):
        defaults = _integration(
            "prod-ae",
            {"baseUrl": "http://old/rest", "orgCode": "PROD", "authMode": "ae_session"},
            is_default=True,
        )
        db = _make_db([defaults])
        merged = resolve_integration_config(
            db,
            tenant_id="t1",
            system="automationedge",
            node_config={
                "baseUrl": "http://new/rest",
                "workflowName": "wf",
            },
            required_fields=("baseUrl", "orgCode", "workflowName"),
        )
        assert merged["baseUrl"] == "http://new/rest"   # node won
        assert merged["orgCode"] == "PROD"              # integration kept
        assert merged["authMode"] == "ae_session"

    def test_empty_node_values_do_not_override_integration(self):
        defaults = _integration(
            "prod-ae",
            {"baseUrl": "http://default/rest", "orgCode": "PROD"},
            is_default=True,
        )
        db = _make_db([defaults])
        merged = resolve_integration_config(
            db,
            tenant_id="t1",
            system="automationedge",
            node_config={"baseUrl": "", "orgCode": None, "workflowName": "wf"},
            required_fields=("baseUrl", "orgCode"),
        )
        assert merged["baseUrl"] == "http://default/rest"
        assert merged["orgCode"] == "PROD"

    def test_unknown_label_raises(self):
        db = _make_db([_integration("other", {"baseUrl": "http://x/rest"})])
        with pytest.raises(IntegrationConfigError, match="No automationedge integration"):
            resolve_integration_config(
                db,
                tenant_id="t1",
                system="automationedge",
                node_config={"integrationLabel": "does-not-exist"},
                required_fields=("baseUrl",),
            )

    def test_missing_required_fields_raise(self):
        db = _make_db([])
        with pytest.raises(IntegrationConfigError, match=r"missing required field\(s\)"):
            resolve_integration_config(
                db,
                tenant_id="t1",
                system="automationedge",
                node_config={"baseUrl": "http://ae/rest"},  # orgCode missing
                required_fields=("baseUrl", "orgCode"),
            )

    def test_integration_label_itself_is_not_merged_into_result(self):
        defaults = _integration("d", {"baseUrl": "http://x"}, is_default=True)
        db = _make_db([defaults])
        merged = resolve_integration_config(
            db,
            tenant_id="t1",
            system="automationedge",
            node_config={"integrationLabel": "d"},
            required_fields=("baseUrl",),
        )
        assert "integrationLabel" not in merged
