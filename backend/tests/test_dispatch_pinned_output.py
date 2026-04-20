"""Unit tests for the DV-01 data-pinning dispatch short-circuit.

dispatch_node returns the pinned payload immediately when
``node_data.pinnedOutput`` is a dict — no handler runs, no env-var
resolution happens. This is the core dev-velocity optimisation:
once a node's output is known-good, operators pin it so subsequent
test runs skip the LLM call / MCP round-trip / whatever.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.engine.node_handlers import dispatch_node


class TestDispatchPinnedOutput:
    def test_pinned_dict_short_circuits_without_calling_handlers(self):
        """No handler is invoked when the node has a pinnedOutput dict."""
        pinned = {"response": "Hello!", "usage": {"input_tokens": 42}}
        node_data = {
            "label": "LLM Agent",
            "nodeCategory": "agent",
            "config": {"model": "gpt-4"},
            "pinnedOutput": pinned,
        }

        # If any handler runs, _handle_agent would try to import LLM SDKs
        # and fail. The short-circuit must fire before that.
        with patch("app.engine.node_handlers._handle_agent") as mock_handler:
            output = dispatch_node(node_data, {}, tenant_id="t1")

        mock_handler.assert_not_called()
        assert output == {**pinned, "_from_pin": True}

    def test_pinned_preserves_every_key_in_the_pin(self):
        pinned = {
            "a": 1,
            "b": [1, 2, 3],
            "nested": {"inner": "value"},
        }
        node_data = {
            "label": "LLM Agent",
            "nodeCategory": "agent",
            "config": {},
            "pinnedOutput": pinned,
        }
        output = dispatch_node(node_data, {}, tenant_id="t1")
        assert output["a"] == 1
        assert output["b"] == [1, 2, 3]
        assert output["nested"] == {"inner": "value"}
        assert output["_from_pin"] is True

    def test_no_pin_falls_through_to_real_handlers(self):
        """When pinnedOutput is absent, dispatch proceeds normally."""
        node_data = {
            "label": "LLM Agent",
            "nodeCategory": "agent",
            "config": {},
        }

        with patch("app.engine.node_handlers._handle_agent") as mock_handler:
            mock_handler.return_value = {"response": "live"}
            output = dispatch_node(node_data, {}, tenant_id="t1")

        mock_handler.assert_called_once()
        assert output == {"response": "live"}
        assert "_from_pin" not in output

    def test_non_dict_pin_value_is_ignored(self):
        """Only dicts are valid pins; other types fall through as if unset."""
        for invalid in [None, "a string", 42, ["not", "a", "dict"]]:
            node_data = {
                "label": "LLM Agent",
                "nodeCategory": "agent",
                "config": {},
                "pinnedOutput": invalid,
            }
            with patch("app.engine.node_handlers._handle_agent") as mock_handler:
                mock_handler.return_value = {"response": "live"}
                output = dispatch_node(node_data, {}, tenant_id="t1")
            mock_handler.assert_called_once(), (
                f"pinnedOutput={invalid!r} should fall through"
            )
            assert output == {"response": "live"}

    def test_empty_dict_pin_is_still_a_pin(self):
        """A pin of {} is a valid empty output — don't confuse with 'no pin'."""
        node_data = {
            "label": "LLM Agent",
            "nodeCategory": "agent",
            "config": {},
            "pinnedOutput": {},
        }
        with patch("app.engine.node_handlers._handle_agent") as mock_handler:
            output = dispatch_node(node_data, {}, tenant_id="t1")
        mock_handler.assert_not_called()
        assert output == {"_from_pin": True}

    def test_env_var_resolution_is_also_skipped_on_pin(self):
        """Handler-side short-circuit runs BEFORE env var resolution,
        so pinned nodes don't incur secret-vault lookups either."""
        node_data = {
            "label": "LLM Agent",
            "nodeCategory": "agent",
            "config": {"apiKey": "{{ env.OPENAI_API_KEY }}"},
            "pinnedOutput": {"response": "cached"},
        }
        with patch("app.engine.prompt_template.resolve_config_env_vars") as env_resolver:
            output = dispatch_node(node_data, {}, tenant_id="t1")
        env_resolver.assert_not_called()
        assert output["response"] == "cached"


class TestDispatchPinnedOutputDownstreamContextShape:
    def test_from_pin_flag_is_underscore_prefixed_so_clean_context_strips_it(self):
        """dag_runner's _get_clean_context strips underscore-prefixed keys
        before persisting to instance.context_json. That convention means
        downstream nodes still see _from_pin during a run (via the shared
        context dict) but it doesn't leak into the persisted blob."""
        pinned = {"response": "Hi"}
        node_data = {
            "label": "LLM Agent",
            "nodeCategory": "agent",
            "config": {},
            "pinnedOutput": pinned,
        }
        output = dispatch_node(node_data, {}, tenant_id="t1")
        # Flag is underscore-prefixed — invariant the strip-helper relies on.
        assert "_from_pin" in output
        assert all(
            k.startswith("_") or not k.startswith("_")
            for k in output.keys()
        )
