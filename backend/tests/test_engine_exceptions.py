"""Unit tests for engine control-flow exceptions."""

import pytest

from app.engine.exceptions import NodeSuspendedAsync


class TestNodeSuspendedAsync:
    def test_carries_async_job_id_system_and_external_job_id(self):
        exc = NodeSuspendedAsync(
            async_job_id="11111111-1111-1111-1111-111111111111",
            system="automationedge",
            external_job_id="2968",
        )
        assert exc.async_job_id == "11111111-1111-1111-1111-111111111111"
        assert exc.system == "automationedge"
        assert exc.external_job_id == "2968"

    def test_str_includes_identifiers_for_log_readability(self):
        exc = NodeSuspendedAsync(
            async_job_id="abc",
            system="automationedge",
            external_job_id="2968",
        )
        msg = str(exc)
        assert "automationedge" in msg
        assert "2968" in msg
        assert "abc" in msg

    def test_is_an_exception(self):
        exc = NodeSuspendedAsync("a", "s", "e")
        assert isinstance(exc, Exception)
        # Catchable via bare except, but the whole point is that dag_runner
        # catches THIS type specifically before the generic Exception fallback.
        with pytest.raises(NodeSuspendedAsync):
            raise exc
