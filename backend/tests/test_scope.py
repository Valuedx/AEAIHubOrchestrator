"""CTX-MGMT.C v2.a — runtime enforcement of ``dependsOn`` for Jinja.

C v1 (already shipped) made ``dependsOn`` informational; the lint
``jinja_ref_outside_depends_on`` warns at promote time but the
runtime ignored the declaration. C v2.a flips that for Jinja
renders only — the ``safe_eval`` and ``build_structured_context_block``
sites are deferred to v2.b / v2.c.

These tests cover three layers:
    - ``scope.py`` pure helpers (get_depends_on, collect_alias_index,
      build_scoped_safe_context, set/get current_node_data)
    - ``render_prompt`` integration with the scope filter
    - The thread-local stash semantics (no leak between threads)
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.engine.prompt_template import render_prompt
from app.engine.scope import (
    build_scoped_safe_context,
    clear_current_node_data,
    collect_alias_index,
    get_current_node_data,
    get_depends_on,
    is_scope_enforced,
    set_current_node_data,
)


# ---------------------------------------------------------------------------
# get_depends_on — both node shapes, malformed values
# ---------------------------------------------------------------------------


class TestGetDependsOn:
    def test_returns_none_when_unset(self):
        assert get_depends_on({"config": {}}) is None

    def test_returns_none_for_non_dict_input(self):
        assert get_depends_on(None) is None
        assert get_depends_on("string") is None
        assert get_depends_on([]) is None

    def test_handler_input_shape(self):
        # Most handlers receive node_data as {label, config, ...}
        node = {"config": {"dependsOn": ["node_a", "node_b"]}}
        assert get_depends_on(node) == ["node_a", "node_b"]

    def test_graph_definition_shape(self):
        # Workflow definition stores it under data.config.
        node = {"id": "n1", "data": {"config": {"dependsOn": ["node_a"]}}}
        assert get_depends_on(node) == ["node_a"]

    def test_empty_list_is_meaningful(self):
        # Author explicitly declared "no upstream node visible".
        node = {"config": {"dependsOn": []}}
        assert get_depends_on(node) == []

    def test_malformed_dependsOn_returns_none(self):
        # Non-list values are treated as unset (not a hard failure
        # because graphs may carry junk during authoring).
        assert get_depends_on({"config": {"dependsOn": "node_a"}}) is None
        assert get_depends_on({"config": {"dependsOn": 42}}) is None

    def test_skips_non_string_entries(self):
        node = {"config": {"dependsOn": ["node_a", 42, None, "node_b"]}}
        assert get_depends_on(node) == ["node_a", "node_b"]

    def test_strips_whitespace(self):
        node = {"config": {"dependsOn": ["  node_a ", "node_b\n"]}}
        assert get_depends_on(node) == ["node_a", "node_b"]


# ---------------------------------------------------------------------------
# collect_alias_index
# ---------------------------------------------------------------------------


class TestCollectAliasIndex:
    def test_empty_when_nodes_map_is_none(self):
        assert collect_alias_index(None) == {}

    def test_empty_when_no_aliases_declared(self):
        nodes_map = {
            "node_a": {"config": {}},
            "node_b": {"data": {"config": {}}},
        }
        assert collect_alias_index(nodes_map) == {}

    def test_collects_handler_shape_aliases(self):
        nodes_map = {
            "node_a": {"config": {"exposeAs": "case"}},
            "node_b": {"config": {"exposeAs": "alert"}},
        }
        idx = collect_alias_index(nodes_map)
        assert idx == {"case": "node_a", "alert": "node_b"}

    def test_collects_graph_definition_shape_aliases(self):
        nodes_map = {
            "node_a": {"data": {"config": {"exposeAs": "case"}}},
            "node_b": {"data": {"config": {"exposeAs": "alert"}}},
        }
        idx = collect_alias_index(nodes_map)
        assert idx == {"case": "node_a", "alert": "node_b"}

    def test_strips_alias_whitespace(self):
        nodes_map = {"node_a": {"config": {"exposeAs": "  case  "}}}
        assert collect_alias_index(nodes_map) == {"case": "node_a"}

    def test_skips_empty_or_non_string_aliases(self):
        nodes_map = {
            "node_a": {"config": {"exposeAs": ""}},
            "node_b": {"config": {"exposeAs": "  "}},
            "node_c": {"config": {"exposeAs": 42}},
            "node_d": {"config": {"exposeAs": "real"}},
        }
        assert collect_alias_index(nodes_map) == {"real": "node_d"}


# ---------------------------------------------------------------------------
# build_scoped_safe_context — the heart of the filter
# ---------------------------------------------------------------------------


class TestBuildScopedSafeContext:
    def _ctx(self, **extras):
        # A typical mid-run context shape.
        base = {
            "trigger": {"message": "user said X"},
            "_runtime": {"loop_index": 0},
            "_loop_item": {"id": "loop"},
            "_instance_id": "inst-1",
            "node_a": {"id": "a", "value": 1},
            "node_b": {"id": "b", "value": 2},
            "node_c": {"id": "c", "value": 3},
        }
        base.update(extras)
        return base

    def test_no_filter_when_dependsOn_unset(self):
        ctx = self._ctx()
        out = build_scoped_safe_context(ctx, {"config": {}}, None)
        # Returned reference unchanged — same object, no copy.
        assert out is ctx

    def test_filter_keeps_only_declared_deps(self):
        ctx = self._ctx()
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_scoped_safe_context(ctx, node, None)
        # node_a kept; node_b and node_c dropped.
        assert "node_a" in out
        assert "node_b" not in out
        assert "node_c" not in out
        # Infrastructure preserved.
        assert "trigger" in out
        assert "_runtime" in out
        assert "_loop_item" in out
        assert "_instance_id" in out

    def test_filter_keeps_aliases_of_declared_deps(self):
        ctx = self._ctx(case={"id": "alias-target"})
        nodes_map = {
            "node_a": {"config": {"exposeAs": "case"}},
            "node_b": {"config": {"exposeAs": "alert"}},
            "node_c": {"config": {}},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_scoped_safe_context(ctx, node, nodes_map)
        # node_a + its alias `case` both kept.
        assert "node_a" in out
        assert "case" in out
        # node_b dropped AND its alias `alert` would be dropped too
        # (not in ctx in this test, but verify the filter path).
        assert "node_b" not in out

    def test_filter_drops_aliases_of_undeclared_nodes(self):
        # `alert` in context maps back to node_b via nodes_map; node_b
        # isn't in dependsOn → alert is filtered out even though it
        # doesn't start with "node_".
        ctx = self._ctx(case={"id": "ok"}, alert={"id": "should-drop"})
        nodes_map = {
            "node_a": {"config": {"exposeAs": "case"}},
            "node_b": {"config": {"exposeAs": "alert"}},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_scoped_safe_context(ctx, node, nodes_map)
        assert "case" in out
        assert "alert" not in out

    def test_empty_dependsOn_drops_all_node_slots(self):
        # Author explicitly declared "no upstream node visible". Only
        # infrastructure remains.
        ctx = self._ctx()
        node = {"config": {"dependsOn": []}}
        out = build_scoped_safe_context(ctx, node, None)
        assert "node_a" not in out
        assert "node_b" not in out
        assert "node_c" not in out
        assert "trigger" in out
        assert "_runtime" in out

    def test_returns_new_dict_not_alias_when_filtering(self):
        ctx = self._ctx()
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_scoped_safe_context(ctx, node, None)
        # Filtered → must be a fresh dict so callers can mutate
        # without affecting the engine context.
        assert out is not ctx

    def test_unknown_non_node_keys_treated_as_infrastructure(self):
        # Keys we don't recognize as aliases AND don't start with
        # node_ are infrastructure — keep them.
        ctx = {
            "trigger": {},
            "_runtime": {},
            "approval": {"token": "x"},  # HITL data
            "_loop_item": {},
            "node_a": {"id": "a"},
            "node_b": {"id": "b"},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_scoped_safe_context(ctx, node, None)
        assert "approval" in out
        assert "node_a" in out
        assert "node_b" not in out


# ---------------------------------------------------------------------------
# Thread-local current-node stash
# ---------------------------------------------------------------------------


class TestThreadLocalStash:
    def test_returns_none_initially(self):
        clear_current_node_data()
        assert get_current_node_data() is None

    def test_set_and_get(self):
        node = {"id": "x", "config": {"dependsOn": ["node_a"]}}
        set_current_node_data(node)
        assert get_current_node_data() is node
        clear_current_node_data()

    def test_does_not_leak_between_threads(self):
        # Set in main; check that another thread sees None.
        clear_current_node_data()
        set_current_node_data({"id": "main-thread"})

        seen: list[Any] = []

        def worker():
            seen.append(get_current_node_data())

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        # Worker thread saw None despite main having set it.
        assert seen == [None]
        # Main still has its value.
        assert get_current_node_data()["id"] == "main-thread"
        clear_current_node_data()


# ---------------------------------------------------------------------------
# render_prompt integration
# ---------------------------------------------------------------------------


class TestRenderPromptScope:
    def teardown_method(self):
        # Hygiene — don't let one test's stash bleed into another.
        clear_current_node_data()

    def test_no_dependsOn_renders_full_context(self):
        ctx = {
            "node_a": {"x": "from-a"},
            "node_b": {"x": "from-b"},
        }
        out = render_prompt("{{ node_a.x }} / {{ node_b.x }}", ctx)
        assert out == "from-a / from-b"

    def test_with_dependsOn_undeclared_ref_renders_empty(self):
        ctx = {
            "_runtime": {},
            "node_a": {"x": "from-a"},
            "node_b": {"x": "from-b"},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = render_prompt(
            "{{ node_a.x }} / {{ node_b.x }}", ctx,
            node_data=node, nodes_map=None,
        )
        # node_a still resolves; node_b is not in scope → empty.
        assert out == "from-a / "

    def test_with_dependsOn_declared_alias_resolves(self):
        ctx = {
            "_runtime": {},
            "node_a": {"id": "the-id"},
            "case": {"id": "the-id"},  # exposeAs alias
        }
        nodes_map = {
            "node_a": {"config": {"exposeAs": "case"}},
            "node_b": {"config": {"exposeAs": "alert"}},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = render_prompt(
            "{{ case.id }}", ctx,
            node_data=node, nodes_map=nodes_map,
        )
        assert out == "the-id"

    def test_with_dependsOn_undeclared_alias_renders_empty(self):
        ctx = {
            "_runtime": {},
            "node_b": {"id": "b"},
            "alert": {"id": "b"},  # alias for node_b which isn't declared
        }
        nodes_map = {
            "node_b": {"config": {"exposeAs": "alert"}},
        }
        node = {"config": {"dependsOn": ["node_a"]}}  # node_a doesn't exist; alias for node_b is filtered out
        out = render_prompt(
            "{{ alert.id }}", ctx,
            node_data=node, nodes_map=nodes_map,
        )
        # `alert` isn't in scope → renders empty (permissive undefined).
        assert out == ""

    def test_thread_local_stash_picked_up_when_no_kwargs(self):
        # Simulates the runner setting the stash before dispatch_node.
        ctx = {
            "_runtime": {},
            "node_a": {"x": "from-a"},
            "node_b": {"x": "from-b"},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        set_current_node_data(node)
        try:
            out = render_prompt("{{ node_a.x }}/{{ node_b.x }}", ctx)
        finally:
            clear_current_node_data()
        assert out == "from-a/"

    def test_explicit_kwargs_override_thread_local(self):
        ctx = {
            "_runtime": {},
            "node_a": {"x": "from-a"},
            "node_b": {"x": "from-b"},
        }
        # Stash node_b only.
        set_current_node_data({"config": {"dependsOn": ["node_b"]}})
        try:
            # Pass explicit node_a — explicit wins.
            out = render_prompt(
                "{{ node_a.x }}/{{ node_b.x }}", ctx,
                node_data={"config": {"dependsOn": ["node_a"]}},
            )
        finally:
            clear_current_node_data()
        assert out == "from-a/"

    def test_infrastructure_keys_always_visible(self):
        # trigger, _runtime, _loop_item etc. must remain accessible
        # to templates regardless of dependsOn.
        ctx = {
            "trigger": {"message": "user said X"},
            "_runtime": {},
            "_loop_item": {"name": "alpha"},
            "node_a": {"x": "from-a"},
            "node_b": {"x": "from-b"},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = render_prompt(
            "{{ trigger.message }} | {{ node_a.x }} | {{ node_b.x }}", ctx,
            node_data=node,
        )
        assert out == "user said X | from-a | "

    def test_empty_dependsOn_drops_all_node_slots(self):
        ctx = {
            "_runtime": {},
            "trigger": {"message": "Y"},
            "node_a": {"x": "a"},
            "node_b": {"x": "b"},
        }
        node = {"config": {"dependsOn": []}}
        out = render_prompt(
            "{{ trigger.message }}|{{ node_a.x }}|{{ node_b.x }}", ctx,
            node_data=node,
        )
        # Trigger renders; both nodes go to empty.
        assert out == "Y||"


# ---------------------------------------------------------------------------
# is_scope_enforced
# ---------------------------------------------------------------------------


class TestIsScopeEnforced:
    def test_false_when_unset(self):
        assert is_scope_enforced({"config": {}}) is False

    def test_true_when_dependsOn_declared(self):
        assert is_scope_enforced({"config": {"dependsOn": ["node_a"]}}) is True

    def test_true_when_dependsOn_empty(self):
        # Empty list is meaningful: "nothing visible".
        assert is_scope_enforced({"config": {"dependsOn": []}}) is True
