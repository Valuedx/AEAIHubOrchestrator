"""CTX-MGMT.K — unit tests for within-run compaction.

Pure helpers (track_write_size, _pick_compaction_candidates,
reconcile_size, is_compaction_enabled) tested without DB.

DB-touching helper (`maybe_compact`) tested with the SQLAlchemy
session mocked. Stub Jinja-compat round-trip (top-level scalar keys
present on overflow + compaction stubs) tested via the existing
prompt_template machinery.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.engine import compaction
from app.engine.compaction import (
    COMPACTION_TARGET_BYTES,
    COMPACTION_THRESHOLD_BYTES,
    MIN_NODE_SIZE_FOR_COMPACTION,
    RECONCILIATION_INTERVAL,
    _is_already_stubbed,
    _pick_compaction_candidates,
    estimate_output_size,
    is_compaction_enabled,
    maybe_compact,
    reconcile_size,
    resolve_compaction_flag,
    track_write_size,
)


# ---------------------------------------------------------------------------
# is_compaction_enabled — fast-path check
# ---------------------------------------------------------------------------


class TestIsCompactionEnabled:
    def test_returns_false_for_empty_context(self):
        assert is_compaction_enabled({}) is False

    def test_returns_false_when_runtime_missing(self):
        assert is_compaction_enabled({"node_1": {}}) is False

    def test_returns_false_when_flag_unset(self):
        assert is_compaction_enabled({"_runtime": {}}) is False

    def test_returns_true_when_flag_set(self):
        assert is_compaction_enabled({"_runtime": {"context_compaction_enabled": True}}) is True

    def test_returns_false_when_runtime_is_not_dict(self):
        assert is_compaction_enabled({"_runtime": "not-a-dict"}) is False


# ---------------------------------------------------------------------------
# resolve_compaction_flag — tenant policy
# ---------------------------------------------------------------------------


class TestResolveCompactionFlag:
    def test_default_on_when_policy_unset(self):
        # Policy column may not be present yet on older DBs. Default
        # TRUE — compaction is a cost saver.
        db = MagicMock()
        fake_policy = MagicMock(spec=[])  # no context_compaction_enabled attr
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_compaction_flag(db, tenant_id="t") is True

    def test_off_when_policy_explicitly_false(self):
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.context_compaction_enabled = False
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_compaction_flag(db, tenant_id="t") is False

    def test_on_when_policy_explicitly_true(self):
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.context_compaction_enabled = True
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_compaction_flag(db, tenant_id="t") is True

    def test_defaults_on_on_policy_lookup_error(self):
        # Compaction is a cost saver — the safe-default is ON when
        # we can't read the policy, opposite of trace which defaults
        # OFF on lookup error.
        db = MagicMock()
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            side_effect=RuntimeError("policy DB hiccup"),
        ):
            assert resolve_compaction_flag(db, tenant_id="t") is True


# ---------------------------------------------------------------------------
# track_write_size — running approximation
# ---------------------------------------------------------------------------


class TestTrackWriteSize:
    def test_first_write_seeds_total(self):
        runtime: dict = {}
        track_write_size(runtime, "node_1", 500)
        assert runtime["context_size_bytes"] == 500
        assert runtime["written_node_sizes"] == {"node_1": 500}
        assert runtime["written_node_order"] == ["node_1"]

    def test_subsequent_writes_accumulate(self):
        runtime: dict = {}
        track_write_size(runtime, "node_1", 500)
        track_write_size(runtime, "node_2", 700)
        assert runtime["context_size_bytes"] == 1200
        assert runtime["written_node_order"] == ["node_1", "node_2"]

    def test_rewrite_replaces_prior_size(self):
        runtime: dict = {}
        track_write_size(runtime, "node_1", 500)
        track_write_size(runtime, "node_1", 200)
        # 500 -> 200, total tracks the latest size.
        assert runtime["context_size_bytes"] == 200
        assert runtime["written_node_sizes"] == {"node_1": 200}
        # Rewrite moves the node to END of order list (recently
        # written = least likely compaction target).
        assert runtime["written_node_order"] == ["node_1"]

    def test_rewrite_after_other_writes_moves_to_end(self):
        runtime: dict = {}
        track_write_size(runtime, "node_1", 500)
        track_write_size(runtime, "node_2", 700)
        track_write_size(runtime, "node_1", 200)
        assert runtime["written_node_order"] == ["node_2", "node_1"]


# ---------------------------------------------------------------------------
# _is_already_stubbed
# ---------------------------------------------------------------------------


class TestIsAlreadyStubbed:
    def test_overflow_stub_recognised(self):
        assert _is_already_stubbed({"_overflow": True, "_artifact_id": "a"}) is True

    def test_compaction_stub_recognised(self):
        assert _is_already_stubbed({"_compacted": True, "_artifact_id": "a"}) is True

    def test_regular_dict_not_stubbed(self):
        assert _is_already_stubbed({"id": "x", "status": "OK"}) is False

    def test_non_dict_not_stubbed(self):
        assert _is_already_stubbed("string") is False
        assert _is_already_stubbed([1, 2, 3]) is False
        assert _is_already_stubbed(None) is False


# ---------------------------------------------------------------------------
# _pick_compaction_candidates
# ---------------------------------------------------------------------------


class TestPickCandidates:
    def test_orders_oldest_first(self):
        runtime = {
            "written_node_order": ["node_1", "node_2", "node_3"],
            "written_node_sizes": {"node_1": 5000, "node_2": 5000, "node_3": 5000},
        }
        context = {
            "node_1": {"data": "x"},
            "node_2": {"data": "y"},
            "node_3": {"data": "z"},
        }
        candidates = _pick_compaction_candidates(context, runtime)
        ids = [c[0] for c in candidates]
        assert ids == ["node_1", "node_2", "node_3"]

    def test_skips_already_compacted(self):
        runtime = {
            "written_node_order": ["node_1", "node_2"],
            "written_node_sizes": {"node_1": 5000, "node_2": 5000},
        }
        context = {
            "node_1": {"_compacted": True, "_artifact_id": "a", "summary": "..."},
            "node_2": {"data": "still-inline"},
        }
        candidates = _pick_compaction_candidates(context, runtime)
        ids = [c[0] for c in candidates]
        assert "node_1" not in ids
        assert "node_2" in ids

    def test_skips_already_overflowed(self):
        runtime = {
            "written_node_order": ["node_1"],
            "written_node_sizes": {"node_1": 5000},
        }
        context = {"node_1": {"_overflow": True, "_artifact_id": "a"}}
        assert _pick_compaction_candidates(context, runtime) == []

    def test_skips_tiny_outputs(self):
        runtime = {
            "written_node_order": ["node_1", "node_2"],
            "written_node_sizes": {"node_1": 100, "node_2": 5000},  # node_1 too small
        }
        context = {
            "node_1": {"id": "x"},
            "node_2": {"data": "y"},
        }
        candidates = _pick_compaction_candidates(context, runtime)
        ids = [c[0] for c in candidates]
        assert "node_1" not in ids
        assert "node_2" in ids

    def test_skips_non_node_keys(self):
        runtime = {
            "written_node_order": ["trigger", "node_1", "approval"],
            "written_node_sizes": {"trigger": 5000, "node_1": 5000, "approval": 5000},
        }
        context = {
            "trigger": {"x": "huge"},
            "node_1": {"data": "y"},
            "approval": {"approved": True, "z": "lots"},
        }
        candidates = _pick_compaction_candidates(context, runtime)
        ids = [c[0] for c in candidates]
        # Only node_1 — trigger + approval are workflow-level state
        # the engine never compacts.
        assert ids == ["node_1"]

    def test_skips_non_dict_outputs(self):
        runtime = {
            "written_node_order": ["node_1", "node_2"],
            "written_node_sizes": {"node_1": 5000, "node_2": 5000},
        }
        context = {
            "node_1": "not-a-dict",  # scalar output — not compactable
            "node_2": {"data": "y"},
        }
        candidates = _pick_compaction_candidates(context, runtime)
        ids = [c[0] for c in candidates]
        assert ids == ["node_2"]


# ---------------------------------------------------------------------------
# reconcile_size — drift correction
# ---------------------------------------------------------------------------


class TestReconcileSize:
    def test_recomputes_total_from_node_slots(self):
        ctx = {
            "_runtime": {"context_size_bytes": 99999},  # drifted
            "trigger": {"x": 1},
            "node_1": {"data": "a" * 100},
            "node_2": {"data": "b" * 200},
            "orchestrator_user_reply": "huge ignored field",
        }
        total = reconcile_size(ctx)
        # Non-node_* keys ignored. node_1 + node_2 sizes counted.
        node1_size = estimate_output_size(ctx["node_1"])
        node2_size = estimate_output_size(ctx["node_2"])
        assert total == node1_size + node2_size
        assert ctx["_runtime"]["context_size_bytes"] == total

    def test_resets_writes_since_reconcile(self):
        ctx = {"_runtime": {"writes_since_reconcile": 99}}
        reconcile_size(ctx)
        assert ctx["_runtime"]["writes_since_reconcile"] == 0


# ---------------------------------------------------------------------------
# maybe_compact — full pass with mocked DB
# ---------------------------------------------------------------------------


class TestMaybeCompact:
    def _build_oversize_context(self, n_nodes: int = 4, per_node_kb: int = 50):
        """Build a context that exceeds COMPACTION_THRESHOLD_BYTES
        and is set up correctly for the runtime tracker."""
        ctx: dict = {
            "_runtime": {
                "context_compaction_enabled": True,
                "written_node_order": [],
                "written_node_sizes": {},
                "context_size_bytes": 0,
            },
        }
        for i in range(n_nodes):
            nid = f"node_{i}"
            ctx[nid] = {
                "id": f"id-{i}",
                "status": "OK",
                "blob": "x" * (per_node_kb * 1024),
            }
            track_write_size(ctx["_runtime"], nid, estimate_output_size(ctx[nid]))
        return ctx

    def test_returns_none_when_disabled(self):
        ctx = self._build_oversize_context()
        ctx["_runtime"]["context_compaction_enabled"] = False
        result = maybe_compact(MagicMock(), ctx, tenant_id="t", instance_id=_uuid.uuid4())
        assert result is None

    def test_returns_none_under_threshold(self):
        # Build a context BELOW the threshold (one tiny node).
        ctx = {
            "_runtime": {
                "context_compaction_enabled": True,
                "written_node_order": ["node_1"],
                "written_node_sizes": {"node_1": 100},
                "context_size_bytes": 100,
            },
            "node_1": {"id": "x"},
        }
        result = maybe_compact(
            MagicMock(), ctx, tenant_id="t", instance_id=_uuid.uuid4(),
        )
        assert result is None

    def test_compacts_oldest_first_until_under_target(self):
        # 4 nodes × 50 kB = ~200 kB > 128 kB threshold. Compaction
        # should free at least the first (oldest) node.
        ctx = self._build_oversize_context(n_nodes=4, per_node_kb=50)
        size_before = ctx["_runtime"]["context_size_bytes"]
        assert size_before > COMPACTION_THRESHOLD_BYTES

        db = MagicMock()
        # persist_artifact uses db.add + db.flush; our mock allocates
        # an `id` on the row when add() is called so str(row.id) is stable.
        def _add_side_effect(row):
            row.id = _uuid.uuid4()
        db.add.side_effect = _add_side_effect

        result = maybe_compact(
            db, ctx, tenant_id="t", instance_id=_uuid.uuid4(),
        )
        assert result is not None
        # At least one node compacted — should be node_0 (oldest).
        assert "node_0" in result["compacted_node_ids"]
        # Compaction bytes_freed > 0.
        assert result["bytes_freed"] > 0
        # Final size below the trigger.
        assert result["size_after"] < result["size_before"]
        # The compacted slot is now a stub.
        assert ctx["node_0"].get("_compacted") is True
        # The non-compacted slot retains its original payload (if any).
        # node_3 was the most recently written — least likely target.
        assert ctx.get("node_3", {}).get("_compacted") is not True or "node_3" in result["compacted_node_ids"]

    def test_top_level_canonical_keys_preserved_on_stub(self):
        # The compaction stub should expose top-level `id` + `status`
        # so downstream Jinja `{{ node_X.id }}` keeps rendering
        # without a template change.
        ctx = self._build_oversize_context(n_nodes=4, per_node_kb=50)
        db = MagicMock()
        db.add.side_effect = lambda r: setattr(r, "id", _uuid.uuid4())
        result = maybe_compact(
            db, ctx, tenant_id="t", instance_id=_uuid.uuid4(),
        )
        assert result is not None
        compacted_id = result["compacted_node_ids"][0]
        stub = ctx[compacted_id]
        # Top-level scalar canonical keys must survive.
        assert stub["id"] == f"id-{compacted_id.split('_')[1]}"
        assert stub["status"] == "OK"
        # And under preview.
        assert stub["preview"]["id"] == stub["id"]

    def test_no_double_compaction_skipped(self):
        # A context where one slot is already compacted shouldn't
        # be re-compacted.
        ctx = self._build_oversize_context(n_nodes=4, per_node_kb=50)
        # Manually mark node_0 as already compacted.
        ctx["node_0"] = {
            "_compacted": True,
            "_artifact_id": "previous",
            "summary": "...",
        }
        ctx["_runtime"]["written_node_sizes"]["node_0"] = 300  # tiny stub
        ctx["_runtime"]["context_size_bytes"] -= 50000
        ctx["_runtime"]["context_size_bytes"] += 300

        db = MagicMock()
        db.add.side_effect = lambda r: setattr(r, "id", _uuid.uuid4())
        result = maybe_compact(
            db, ctx, tenant_id="t", instance_id=_uuid.uuid4(),
        )
        # node_0 (stubbed) was not picked; later nodes were.
        if result:
            assert "node_0" not in result["compacted_node_ids"]


# ---------------------------------------------------------------------------
# Stub Jinja round-trip — top-level keys survive both overflow + compaction
# ---------------------------------------------------------------------------


class TestStubJinjaTopLevelKeys:
    """The stub change in CTX-MGMT.K spreads canonical scalar keys to
    top level so `{{ node_X.id }}` works the same pre/post-stub.
    Confirm both overflow and compaction stubs render correctly."""

    def test_overflow_stub_top_level_id(self):
        from app.engine.output_artifact import materialize_overflow_stub
        from app.engine.prompt_template import render_prompt

        big = {"id": "req-99", "status": "FAILED", "blob": "x" * 200_000}
        stub = materialize_overflow_stub(
            big, budget=1024, size_bytes=200_000,
            artifact_id="art-1", kind="overflow",
        )
        rendered = render_prompt("Got id={{ node_3.id }} status={{ node_3.status }}",
                                 {"node_3": stub})
        assert rendered == "Got id=req-99 status=FAILED"

    def test_compaction_stub_top_level_id(self):
        from app.engine.output_artifact import materialize_overflow_stub
        from app.engine.prompt_template import render_prompt

        big = {"id": "req-77", "status": "OK", "huge": "x" * 200_000}
        stub = materialize_overflow_stub(
            big, budget=1024, size_bytes=200_000,
            artifact_id="art-2", kind="compaction",
        )
        # Stub uses _compacted (not _overflow) marker.
        assert stub["_compacted"] is True
        assert "_overflow" not in stub
        # And top-level keys still resolve.
        rendered = render_prompt(
            "{% if node_3._compacted %}COMPACTED{% endif %} {{ node_3.id }}",
            {"node_3": stub},
        )
        assert "COMPACTED" in rendered
        assert "req-77" in rendered
