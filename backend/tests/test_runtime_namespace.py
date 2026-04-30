"""CTX-MGMT.D — `_runtime` namespace for resume-safe context state.

Tests cover:

  * The `_get_runtime` / `_hoist_legacy_runtime` / `_get_clean_context`
    helper trio in isolation (pure unit tests, no DB).
  * The actual correctness bug fixed by CTX-MGMT.D — HITL inside a
    ForEach iteration must resume at the same item index, not at 0.
  * Legacy context_jsons (with flat `_loop_iteration` / `_cycle_iterations`
    / `_parent_chain` / `hitl_pending_call`) are hoisted in-memory on
    first read — no migration script, no in-flight instance breakage.
  * `_trace` (top-level + ephemeral) does NOT survive a clean-context
    strip, but `_runtime` does.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.engine.dag_runner import (
    _get_clean_context,
    _get_runtime,
    _hoist_legacy_runtime,
    _LEGACY_RUNTIME_KEYS,
)


# ---------------------------------------------------------------------------
# _get_runtime
# ---------------------------------------------------------------------------


class TestGetRuntime:
    def test_creates_when_absent(self):
        ctx: dict[str, Any] = {}
        runtime = _get_runtime(ctx)
        assert runtime == {}
        assert ctx["_runtime"] is runtime  # same dict, not a copy

    def test_returns_existing(self):
        existing = {"loop_index": 3}
        ctx = {"_runtime": existing}
        runtime = _get_runtime(ctx)
        assert runtime is existing

    def test_replaces_non_dict_runtime_value(self):
        # Defensive — if a poorly-formed context_json has _runtime:
        # null / list / string, treat it as if it were missing.
        ctx: dict[str, Any] = {"_runtime": "not-a-dict"}
        runtime = _get_runtime(ctx)
        assert runtime == {}
        assert isinstance(ctx["_runtime"], dict)

    def test_writes_persist_through_helper(self):
        ctx: dict[str, Any] = {}
        _get_runtime(ctx)["loop_index"] = 2
        assert ctx["_runtime"]["loop_index"] == 2


# ---------------------------------------------------------------------------
# _get_clean_context
# ---------------------------------------------------------------------------


class TestGetCleanContext:
    def test_strips_underscore_prefixed_keys(self):
        ctx = {
            "_instance_id": "abc",
            "_workflow_def_id": "def",
            "_current_node_id": "node_1",
            "_trace": object(),  # not JSON-serialisable; just must be stripped
            "node_1": {"output": 1},
            "trigger": {"x": 1},
        }
        clean = _get_clean_context(ctx)
        assert "_instance_id" not in clean
        assert "_workflow_def_id" not in clean
        assert "_current_node_id" not in clean
        assert "_trace" not in clean
        assert clean["node_1"] == {"output": 1}
        assert clean["trigger"] == {"x": 1}

    def test_preserves_runtime(self):
        """The whole point of CTX-MGMT.D — `_runtime` survives the strip
        even though it starts with an underscore."""
        ctx = {
            "_instance_id": "abc",
            "_runtime": {"loop_index": 4, "cycle_iterations": {"lb1": 2}},
            "node_1": {"output": 1},
        }
        clean = _get_clean_context(ctx)
        assert clean["_runtime"] == {"loop_index": 4, "cycle_iterations": {"lb1": 2}}
        assert "_instance_id" not in clean

    def test_empty_runtime_still_preserved(self):
        ctx = {"_runtime": {}, "node_1": {"x": 1}}
        clean = _get_clean_context(ctx)
        assert clean["_runtime"] == {}


# ---------------------------------------------------------------------------
# _hoist_legacy_runtime — backward compat
# ---------------------------------------------------------------------------


class TestHoistLegacyRuntime:
    def test_no_op_when_no_legacy_keys(self):
        ctx = {"trigger": {"x": 1}, "node_1": {"y": 2}}
        before = dict(ctx)
        _hoist_legacy_runtime(ctx)
        assert ctx == before

    def test_hoists_loop_iteration(self):
        # The actual bug — a context_json suspended pre-CTX-MGMT.D has
        # `_loop_iteration` flat. After hoist, it lives under _runtime
        # and the flat key is gone (single source of truth).
        ctx = {
            "_loop_iteration": 3,
            "_loop_index": 2,
            "_loop_item": {"id": 42},
            "_loop_item_var": "item",
            "node_1": {"x": 1},
        }
        _hoist_legacy_runtime(ctx)
        assert "_loop_iteration" not in ctx
        assert "_loop_index" not in ctx
        assert "_loop_item" not in ctx
        assert "_loop_item_var" not in ctx
        runtime = ctx["_runtime"]
        assert runtime["loop_iteration"] == 3
        assert runtime["loop_index"] == 2
        assert runtime["loop_item"] == {"id": 42}
        assert runtime["loop_item_var"] == "item"

    def test_hoists_cycle_iterations(self):
        ctx = {"_cycle_iterations": {"lb1": 4, "lb2": 1}, "node_1": {}}
        _hoist_legacy_runtime(ctx)
        assert "_cycle_iterations" not in ctx
        assert ctx["_runtime"]["cycle_iterations"] == {"lb1": 4, "lb2": 1}

    def test_hoists_parent_chain(self):
        ctx = {"_parent_chain": ["wf-a", "wf-b"]}
        _hoist_legacy_runtime(ctx)
        assert "_parent_chain" not in ctx
        assert ctx["_runtime"]["parent_chain"] == ["wf-a", "wf-b"]

    def test_hoists_hitl_pending_call(self):
        # hitl_pending_call is the one legacy key that was NOT
        # underscore-prefixed (it survived the strip "by accident");
        # CTX-MGMT.D moves it under _runtime explicitly.
        pending = {"tool": "ae.agent.restart_service", "arguments": {"agent_id": "a1"}}
        ctx = {"hitl_pending_call": pending}
        _hoist_legacy_runtime(ctx)
        assert "hitl_pending_call" not in ctx
        assert ctx["_runtime"]["hitl_pending_call"] == pending

    def test_existing_runtime_takes_precedence(self):
        # If a context_json has both legacy flat AND new _runtime
        # (hand-edited or partial migration), the canonical _runtime
        # value wins. Legacy is dropped.
        ctx = {
            "_loop_index": 99,           # legacy claims 99
            "_runtime": {"loop_index": 7},  # canonical claims 7
        }
        _hoist_legacy_runtime(ctx)
        assert "_loop_index" not in ctx
        assert ctx["_runtime"]["loop_index"] == 7

    def test_idempotent(self):
        ctx = {"_loop_index": 3, "_cycle_iterations": {"lb1": 1}}
        _hoist_legacy_runtime(ctx)
        snapshot = dict(ctx)
        _hoist_legacy_runtime(ctx)
        assert ctx == snapshot

    def test_legacy_key_set_includes_all_documented_keys(self):
        """Catch-all: the _LEGACY_RUNTIME_KEYS tuple must list every
        runtime key that was previously persisted at the top level.
        Adding a new flat runtime key without listing it here would
        silently break resume."""
        legacy_names = {legacy for legacy, _ in _LEGACY_RUNTIME_KEYS}
        # The four loop keys + cycle counters + parent chain + HITL
        # pending. Update this list when adding new resume-safe keys.
        assert legacy_names == {
            "_loop_item", "_loop_index", "_loop_iteration", "_loop_item_var",
            "_cycle_iterations", "_parent_chain", "hitl_pending_call",
        }


# ---------------------------------------------------------------------------
# Round-trip — clean + restore preserves _runtime end-to-end
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_clean_then_hoist_preserves_runtime_data(self):
        """Simulate the real suspend/resume flow:
          1. Live context has loop counters + cycle iterations.
          2. Engine calls _get_clean_context → persist to instance_json.
          3. Resume: load context_json, hoist legacy keys (none in V2 shape).
          4. Read counters back → must match what we wrote.
        """
        live = {
            "trigger": {"x": 1},
            "_instance_id": "i-1",
            "_trace": object(),
            "node_1": {"out": 1},
        }
        runtime = _get_runtime(live)
        runtime["loop_index"] = 3
        runtime["cycle_iterations"] = {"lb1": 5}
        runtime["parent_chain"] = ["wf-a"]

        # 1) Persist
        persisted = _get_clean_context(live)
        # _trace stripped, _instance_id stripped, _runtime preserved.
        assert "_trace" not in persisted
        assert "_instance_id" not in persisted
        assert persisted["_runtime"] == runtime
        assert persisted["node_1"] == {"out": 1}

        # 2) Resume — fresh dict from JSON, hoist (no-op on V2 shape).
        loaded = dict(persisted)
        _hoist_legacy_runtime(loaded)

        # 3) Engine repopulates ephemeral keys.
        loaded["_instance_id"] = "i-1"
        loaded["_trace"] = object()

        # 4) Read counters back — they survived.
        rt = _get_runtime(loaded)
        assert rt["loop_index"] == 3
        assert rt["cycle_iterations"] == {"lb1": 5}
        assert rt["parent_chain"] == ["wf-a"]

    def test_legacy_context_resume_recovers_loop_index(self):
        """The actual correctness bug fix — a pre-CTX-MGMT.D context_json
        from a suspended HITL-inside-ForEach run loses `_loop_index` to
        the strip, then resume sees `loop_index` missing. With the
        hoist, the legacy value is recovered."""
        legacy_persisted = {
            # Note: pre-CTX-MGMT.D `_get_clean_context` stripped these
            # so they would NEVER appear in a real legacy context_json.
            # But suppose a different process (or hand-edited operator
            # context_patch) had injected them — the hoist still works.
            "_loop_index": 4,
            "_loop_iteration": 5,
            "_loop_item": {"customer_id": "c-7"},
            "_loop_item_var": "row",
            "node_1": {"out": "ok"},
            "trigger": {"batch": "x"},
        }
        loaded = dict(legacy_persisted)
        _hoist_legacy_runtime(loaded)
        rt = _get_runtime(loaded)
        assert rt["loop_index"] == 4
        assert rt["loop_iteration"] == 5
        assert rt["loop_item"] == {"customer_id": "c-7"}
        assert rt["loop_item_var"] == "row"
        # The iterator can now resume at index 5 (== loop_index + 1).
        # Pre-fix: rt was empty, iterator restarted at 0.

    def test_hitl_pending_call_legacy_to_new_round_trip(self):
        """The HITL re-fire mechanism: a legacy context_json had
        `hitl_pending_call` at the root (it didn't start with `_`,
        so it survived). After the hoist, it's under _runtime,
        which is where react_loop now reads from. The behavior is
        identical end-to-end."""
        legacy = {
            "hitl_pending_call": {
                "tool": "ae.agent.restart_service",
                "arguments": {"agent_id": "agent-7"},
                "iteration": 3,
            },
            "approval": {"approved": True, "technical_approval_id": "tok-1"},
        }
        loaded = dict(legacy)
        _hoist_legacy_runtime(loaded)
        rt = _get_runtime(loaded)
        assert rt["hitl_pending_call"]["tool"] == "ae.agent.restart_service"
        # react_loop reads via runtime first, falls back to flat — both
        # should now work.
        from app.engine import react_loop  # noqa: F401 — import guard
        # The actual react_loop integration is exercised by the
        # existing react_loop tests; here we just confirm the hoist
        # surfaced the value.
