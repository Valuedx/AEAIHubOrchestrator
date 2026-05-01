"""CTX-MGMT.C v2.c — ``build_structured_context_block`` honors
``dependsOn`` for the per-turn user-message bundle.

v2.a (already shipped) filtered the **system prompt** Jinja
namespace. v2.c filters the **per-turn user message** that
``assemble_agent_messages`` builds — it iterates ``context.items()``
and emits each ``node_*`` slot as JSON. Without this slice, a
worker node that declared ``dependsOn=["node_a"]`` would still
see node_b's full output dumped into its user message.

These tests cover the build_structured_context_block helper
directly (its arg surface) AND its integration with
``assemble_agent_messages`` via the thread-local stash that the
runner sets before each ``dispatch_node`` call.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.engine.memory_service import (
    EffectiveMemoryPolicy,
    assemble_agent_messages,
)
from app.engine.prompt_template import build_structured_context_block
from app.engine.scope import (
    clear_current_node_data,
    set_current_node_data,
)


# ---------------------------------------------------------------------------
# Test scaffolding (mirrors test_memory_service.py / test_distill_cache_split.py)
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def query(self, model):
        return _FakeQuery(self._rows)


# ---------------------------------------------------------------------------
# build_structured_context_block — direct arg surface
# ---------------------------------------------------------------------------


class TestBuildStructuredContextBlockScope:
    def teardown_method(self):
        clear_current_node_data()

    def _ctx(self):
        return {
            "trigger": {"message": "user X"},
            "_runtime": {},
            "node_a": {"id": "a", "value": 1},
            "node_b": {"id": "b", "value": 2},
            "node_c": {"id": "c", "value": 3},
        }

    def test_no_dependsOn_emits_every_node(self):
        out = build_structured_context_block(self._ctx())
        # All three node outputs appear.
        assert "**Output of node_a:**" in out
        assert "**Output of node_b:**" in out
        assert "**Output of node_c:**" in out

    def test_with_dependsOn_kwarg_filters_to_declared_only(self):
        ctx = self._ctx()
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_structured_context_block(ctx, node_data=node)
        # node_a kept; node_b and node_c dropped.
        assert "**Output of node_a:**" in out
        assert "**Output of node_b:**" not in out
        assert "**Output of node_c:**" not in out
        # Trigger always emitted (infrastructure, not a node output).
        assert "Trigger input:" in out

    def test_empty_dependsOn_drops_all_nodes_keeps_trigger(self):
        ctx = self._ctx()
        node = {"config": {"dependsOn": []}}
        out = build_structured_context_block(ctx, node_data=node)
        assert "**Output of node_a:**" not in out
        assert "**Output of node_b:**" not in out
        assert "Trigger input:" in out

    def test_thread_local_stash_picked_up(self):
        # Simulates the runner setting the stash before dispatch_node.
        ctx = self._ctx()
        set_current_node_data({"config": {"dependsOn": ["node_b"]}})
        try:
            out = build_structured_context_block(ctx)
        finally:
            clear_current_node_data()
        assert "**Output of node_a:**" not in out
        assert "**Output of node_b:**" in out
        assert "**Output of node_c:**" not in out

    def test_explicit_kwarg_overrides_thread_local(self):
        ctx = self._ctx()
        # Stash node_b only.
        set_current_node_data({"config": {"dependsOn": ["node_b"]}})
        try:
            # But the explicit kwarg picks node_c.
            out = build_structured_context_block(
                ctx,
                node_data={"config": {"dependsOn": ["node_c"]}},
            )
        finally:
            clear_current_node_data()
        assert "**Output of node_b:**" not in out
        assert "**Output of node_c:**" in out

    def test_loop_item_always_emitted(self):
        # Loop item is per-iteration infrastructure, not a node
        # output — must remain visible regardless of scope.
        ctx = {
            "trigger": {"message": "X"},
            "_runtime": {"loop_item": {"name": "alpha"}},
            "node_a": {"value": 1},
            "node_b": {"value": 2},
        }
        node = {"config": {"dependsOn": ["node_a"]}}
        out = build_structured_context_block(ctx, node_data=node)
        assert "Current loop item:" in out
        assert "alpha" in out

    def test_exclude_node_ids_still_works(self):
        # Pre-existing exclude_node_ids arg is independent of scope —
        # both filters apply.
        ctx = self._ctx()
        node = {"config": {"dependsOn": ["node_a", "node_b"]}}
        out = build_structured_context_block(
            ctx,
            node_data=node,
            exclude_node_ids={"node_b"},  # also drop node_b
        )
        assert "**Output of node_a:**" in out
        assert "**Output of node_b:**" not in out
        assert "**Output of node_c:**" not in out

    def test_alias_in_dependsOn_resolves_to_source_node(self):
        # Edge case: author wrote `dependsOn: ["case"]` (the alias)
        # instead of the canonical `["node_a"]`. Should still emit
        # node_a's slot when nodes_map lets us resolve the alias.
        ctx = self._ctx()
        nodes_map = {
            "node_a": {"config": {"exposeAs": "case"}},
            "node_b": {"config": {}},
            "node_c": {"config": {}},
        }
        node = {"config": {"dependsOn": ["case"]}}
        out = build_structured_context_block(
            ctx, node_data=node, nodes_map=nodes_map,
        )
        assert "**Output of node_a:**" in out
        assert "**Output of node_b:**" not in out

    def test_no_filter_when_dependsOn_unset(self):
        # Hot path — when node_data is unset, the structured block
        # behaves identically to pre-v2.c. No surprise filtering.
        ctx = self._ctx()
        out = build_structured_context_block(
            ctx, node_data={"config": {}},  # config without dependsOn
        )
        assert "**Output of node_a:**" in out
        assert "**Output of node_b:**" in out
        assert "**Output of node_c:**" in out


# ---------------------------------------------------------------------------
# Integration via assemble_agent_messages — confirms the runner's
# thread-local stash flows through to the user-message bundle
# ---------------------------------------------------------------------------


class TestAssembleAgentMessagesScope:
    def teardown_method(self):
        clear_current_node_data()

    def _patch_memory_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(enabled=False),
        )

    def _patch_memory_enabled(self, monkeypatch, session):
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(
                history_node_id="node_history",
                instructions=["You are an agent."],
                recent_token_budget=1000,
                max_semantic_hits=0,
            ),
        )
        monkeypatch.setattr(
            "app.engine.memory_service.get_or_create_session",
            lambda *args, **kwargs: session,
        )
        monkeypatch.setattr(
            "app.engine.memory_service.get_active_episode",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.engine.memory_service.retrieve_memory_records",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            "app.engine.memory_service._active_entity_facts",
            lambda *args, **kwargs: [],
        )

    def test_memory_disabled_path_filters_user_message(self, monkeypatch):
        self._patch_memory_disabled(monkeypatch)
        ctx = {
            "trigger": {"message": "user X"},
            "node_a": {"value": "a-data"},
            "node_b": {"value": "b-data"},
        }
        # Runner stashes node_data before dispatch_node — simulate.
        set_current_node_data({"config": {"dependsOn": ["node_a"]}})
        try:
            messages, _ = assemble_agent_messages(
                _FakeDB(),
                tenant_id="t",
                workflow_def_id="",
                context=ctx,
                node_config={},
                rendered_system_prompt="System.",
            )
        finally:
            clear_current_node_data()

        # Two messages: system, user.
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user"]
        user_content = messages[1]["content"]
        # node_a's data appears; node_b's does NOT.
        assert "a-data" in user_content
        assert "b-data" not in user_content

    def test_memory_enabled_path_filters_workflow_context(self, monkeypatch):
        session = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id="t",
            session_id="sess-1",
            summary_text="",
            summary_through_turn=0,
            message_count=0,
            active_episode_id=None,
        )
        self._patch_memory_enabled(monkeypatch, session)

        ctx = {
            "trigger": {"message": "user X"},
            "node_history": {"session_id": "sess-1", "messages": []},
            "node_a": {"value": "a-data"},
            "node_b": {"value": "b-data"},
        }
        set_current_node_data({"config": {"dependsOn": ["node_a"]}})
        try:
            messages, _ = assemble_agent_messages(
                _FakeDB(),
                tenant_id="t",
                workflow_def_id="",
                context=ctx,
                node_config={"historyNodeId": "node_history"},
                rendered_system_prompt="Solve.",
            )
        finally:
            clear_current_node_data()

        # Final user message should contain node_a but NOT node_b in
        # its "Workflow context:" section.
        final_user = messages[-1]["content"]
        assert "Workflow context:" in final_user
        assert "a-data" in final_user
        assert "b-data" not in final_user

    def test_no_dependsOn_emits_everything(self, monkeypatch):
        # Backward-compat: graphs without dependsOn see no filtering.
        self._patch_memory_disabled(monkeypatch)
        ctx = {
            "trigger": {"message": "X"},
            "node_a": {"value": "a-data"},
            "node_b": {"value": "b-data"},
        }
        # No stash — simulates a graph that hasn't declared dependsOn.
        clear_current_node_data()
        messages, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="t",
            workflow_def_id="",
            context=ctx,
            node_config={},
            rendered_system_prompt="System.",
        )
        user_content = messages[1]["content"]
        # Both still appear.
        assert "a-data" in user_content
        assert "b-data" in user_content
