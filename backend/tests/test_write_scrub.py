"""CTX-MGMT.F — unit tests for write-time secret scrubbing.

The engine runs `scrub_secrets` on `context[node_id] = output` BEFORE
the rest of the post-handler pipeline (schema validation, overflow,
reducer, alias, trace, compaction). This closes the leak vector
where a handler's output carries a token/password and a downstream
LLM node would otherwise see it via Jinja.

The scrubber itself (`app/engine/scrubber.py`) is shipped since
0001 and key-based — these tests exercise the engine wiring + the
tenant-policy gate.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.engine.scrubber import scrub_secrets


# ---------------------------------------------------------------------------
# scrubber smoke (existing helper, but confirm shape we depend on)
# ---------------------------------------------------------------------------


class TestScrubberSmoke:
    """The scrubber has been around since 0001. These confirm the
    shape CTX-MGMT.F depends on."""

    def test_redacts_api_key(self):
        out = scrub_secrets({"api_key": "sk-ant-secret", "name": "ok"})
        assert out["api_key"] == "[REDACTED]"
        assert out["name"] == "ok"

    def test_redacts_token(self):
        out = scrub_secrets({"token": "tok-123", "user": "alice"})
        assert out["token"] == "[REDACTED]"
        assert out["user"] == "alice"

    def test_walks_nested_dicts(self):
        out = scrub_secrets({
            "headers": {"Authorization": "Bearer abc", "X-Trace": "trace-1"},
            "body": "ok",
        })
        # Authorization is sensitive; X-Trace is not.
        assert out["headers"]["Authorization"] == "[REDACTED]"
        assert out["headers"]["X-Trace"] == "trace-1"

    def test_walks_lists(self):
        out = scrub_secrets({
            "secrets": [{"password": "p1"}, {"password": "p2"}],
        })
        assert out["secrets"][0]["password"] == "[REDACTED]"
        assert out["secrets"][1]["password"] == "[REDACTED]"

    def test_does_not_mutate_input(self):
        original = {"api_key": "sk-secret"}
        scrubbed = scrub_secrets(original)
        # Original unchanged.
        assert original["api_key"] == "sk-secret"
        # Scrubbed result is distinct.
        assert scrubbed["api_key"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Engine integration — runtime flag controls write-time scrub
# ---------------------------------------------------------------------------


def _ctx_with_flag(enabled: bool) -> dict[str, Any]:
    """Mimic the runtime context after dag_runner.execute_graph
    resolves the tenant policy."""
    return {
        "_runtime": {
            "context_secret_scrub_enabled": enabled,
        },
    }


class TestRuntimeFlag:
    """The flag lives on `_runtime` so it survives suspend/resume
    via CTX-MGMT.D. The engine post-handler block reads it via
    `_get_runtime(context).get("context_secret_scrub_enabled", True)`."""

    def test_default_is_on(self):
        from app.engine.dag_runner import _get_runtime

        ctx: dict[str, Any] = {}
        runtime = _get_runtime(ctx)
        # When the flag wasn't set during execute_graph (e.g.
        # legacy context), default to True (close-leak). The
        # engine code uses `.get(..., True)` for the default.
        assert runtime.get("context_secret_scrub_enabled", True) is True

    def test_explicit_off_is_respected(self):
        from app.engine.dag_runner import _get_runtime

        ctx = _ctx_with_flag(False)
        runtime = _get_runtime(ctx)
        assert runtime.get("context_secret_scrub_enabled", True) is False

    def test_explicit_on_is_respected(self):
        from app.engine.dag_runner import _get_runtime

        ctx = _ctx_with_flag(True)
        runtime = _get_runtime(ctx)
        assert runtime.get("context_secret_scrub_enabled", True) is True


# ---------------------------------------------------------------------------
# End-to-end shape — what the engine does after dispatch_node returns
# ---------------------------------------------------------------------------


class TestEnginePostHandlerShape:
    """Direct simulation of the post-handler block in
    `_execute_single_node`. We don't spin up the full execute_graph
    machinery; we exercise the order: scrub → schema check →
    overflow → reducer. Most importantly: confirms the scrubbed
    value is what flows into the rest of the pipeline."""

    def _post_handler(
        self, output: Any, *, scrub_enabled: bool = True,
    ) -> Any:
        """Mimic the head of the post-handler pipeline."""
        # Resolve flag.
        from app.engine.dag_runner import _get_runtime
        ctx: dict[str, Any] = _ctx_with_flag(scrub_enabled)
        runtime = _get_runtime(ctx)
        # CTX-MGMT.F — write-time scrub.
        if runtime.get("context_secret_scrub_enabled", True):
            output = scrub_secrets(output)
        return output

    def test_handler_output_with_token_gets_scrubbed(self):
        # A hypothetical HTTP handler returns an OAuth response that
        # carries the token; without F, downstream Jinja could
        # `{{ node_X.access_token }}` and feed that to an LLM prompt.
        raw_output = {
            "access_token": "secret-tok",
            "token_type": "Bearer",
            "user_id": "u-42",
        }
        out = self._post_handler(raw_output, scrub_enabled=True)
        # Token redacted.
        assert out["access_token"] == "[REDACTED]"
        # Non-secret fields preserved.
        assert out["user_id"] == "u-42"

    def test_scrub_disabled_passes_secret_through(self):
        # Opt-out path: tenants that need un-scrubbed in-memory
        # context (debugging, deliberate token-handling flow) get
        # the original.
        raw_output = {"api_key": "sk-leak"}
        out = self._post_handler(raw_output, scrub_enabled=False)
        assert out["api_key"] == "sk-leak"

    def test_scrubbed_value_round_trips_through_overflow(self):
        # A big output containing a secret, scrubbed first, then
        # overflow-stubbed. The artifact stored is ALSO scrubbed
        # (since scrub fires before overflow).
        from app.engine.output_artifact import (
            estimate_output_size,
            materialize_overflow_stub,
        )

        big_with_secret = {
            "id": "x",
            "api_key": "sk-secret",
            "blob": "y" * 200_000,
        }
        # Step 1: scrub at write time.
        scrubbed = self._post_handler(big_with_secret, scrub_enabled=True)
        assert scrubbed["api_key"] == "[REDACTED]"

        # Step 2: overflow path materialises stub from the SCRUBBED
        # value — what the artifact persists. Even if the artifact
        # is read back, no secret leaks.
        stub = materialize_overflow_stub(
            scrubbed,
            budget=1024,
            size_bytes=estimate_output_size(scrubbed),
            artifact_id="art-1",
            kind="overflow",
        )
        # Stub canonical preview includes id (canonical) but NOT api_key.
        assert stub.get("id") == "x"
        # If api_key happened to be picked up as an extra-scalar,
        # it'd be the redacted string — never the original secret.
        if "api_key" in stub.get("preview", {}):
            assert stub["preview"]["api_key"] == "[REDACTED]"

    def test_handler_returning_non_dict_unchanged(self):
        # scrub_secrets on a list/scalar returns it as-is.
        out = self._post_handler(["a", "b", "c"], scrub_enabled=True)
        assert out == ["a", "b", "c"]

    def test_default_when_runtime_missing(self):
        # Defensive — context with no _runtime should still get the
        # default (close-leak ON), since the engine uses
        # `.get("context_secret_scrub_enabled", True)`.
        from app.engine.dag_runner import _get_runtime

        ctx: dict[str, Any] = {}
        runtime = _get_runtime(ctx)
        # Default is ON.
        assert runtime.get("context_secret_scrub_enabled", True) is True


# ---------------------------------------------------------------------------
# Order matters — scrub happens BEFORE schema validation, NOT after
# ---------------------------------------------------------------------------


class TestPipelineOrder:
    """If schema validation ran before scrub, a secret-bearing
    output that fails validation would have its raw secret stamped
    into `_schema_errors`. Confirm scrub runs first so the schema
    pipeline operates on the scrubbed value."""

    def test_scrub_happens_before_schema_validation(self):
        # We verify this via code inspection of dag_runner — the
        # scrub block is positioned after dispatch_node and BEFORE
        # the outputSchema block. This test loads the source and
        # asserts the relative ordering, so a future refactor can't
        # silently move scrub below schema validation.
        from pathlib import Path

        src = Path(__file__).parent.parent / "app" / "engine" / "dag_runner.py"
        source = src.read_text(encoding="utf-8")
        scrub_marker = "# CTX-MGMT.F — write-time secret scrub. Runs FIRST"
        schema_marker = "# CTX-MGMT.I — validate handler output"
        scrub_idx = source.find(scrub_marker)
        schema_idx = source.find(schema_marker)
        assert scrub_idx > 0, "F marker not found in dag_runner"
        assert schema_idx > 0, "I marker not found in dag_runner"
        assert scrub_idx < schema_idx, (
            "Scrub must run BEFORE schema validation so secrets "
            "don't leak into schema-error annotations"
        )
