"""Unit tests for the RLS tenant-context helper.

These tests do not require a live Postgres — they verify that
``set_tenant_context`` issues the expected SQL with parameter binding
(defence against SQL injection via tenant_id).
"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from app.database import set_tenant_context


class TestSetTenantContext:
    def test_rejects_empty_tenant_id(self):
        db = MagicMock()
        with pytest.raises(ValueError):
            set_tenant_context(db, "")
        db.execute.assert_not_called()

    def test_rejects_none_tenant_id(self):
        db = MagicMock()
        with pytest.raises(ValueError):
            set_tenant_context(db, None)  # type: ignore[arg-type]
        db.execute.assert_not_called()

    def test_executes_parameterised_set_tenant_id_call(self):
        db = MagicMock()
        set_tenant_context(db, "tenant-abc")
        db.execute.assert_called_once()
        stmt, params = db.execute.call_args[0]
        # SQL text must reference the function, not interpolate the tenant.
        # This is the anti-SQL-injection guarantee.
        sql = str(stmt)
        assert "set_tenant_id" in sql
        assert "tenant-abc" not in sql, (
            "tenant_id must be passed as a bind parameter, not interpolated"
        )
        assert params == {"tid": "tenant-abc"}

    def test_statement_is_sqlalchemy_text_not_raw_string(self):
        db = MagicMock()
        set_tenant_context(db, "t1")
        stmt = db.execute.call_args[0][0]
        # Compare against a fresh text() literal to confirm the shape.
        assert type(stmt).__name__ == type(text("x")).__name__

    def test_special_characters_in_tenant_id_are_bound_not_interpolated(self):
        # A malicious tenant string must never break out of the bind.
        db = MagicMock()
        evil = "abc'); DROP TABLE workflows; --"
        set_tenant_context(db, evil)
        stmt, params = db.execute.call_args[0]
        assert "DROP TABLE" not in str(stmt)
        assert params == {"tid": evil}
