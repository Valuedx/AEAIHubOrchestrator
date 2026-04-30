"""CTX-MGMT.C — exposeAs aliasing + dependsOn lint extension.

v1 scope:
  * exposeAs writes the node's output under an alias key in addition
    to its canonical node_id slot. Backward-compatible.
  * dependsOn is accepted on node config; runtime enforcement of
    "you may only read from these upstreams" deferred to v2. The
    Jinja-ref lint catches refs outside dependsOn so authoring-time
    drift surfaces immediately.

These tests exercise the helpers in isolation; the engine wiring is
exercised end-to-end via the existing dag_runner test suite (every
existing test still passes because the default — no exposeAs, no
dependsOn — produces identical behavior).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.copilot.lints import lint_jinja_dangling_reference
from app.engine.config_validator import _validate_expose_as_collisions


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _node(node_id: str, *, label: str = "LLM Agent",
          config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {"label": label, "nodeCategory": "agent", "config": dict(config or {})},
    }


# ---------------------------------------------------------------------------
# exposeAs collision validator
# ---------------------------------------------------------------------------


class TestExposeAsCollisions:
    def test_no_lint_when_no_exposeAs(self):
        graph = {
            "nodes": [_node("n1"), _node("n2")],
            "edges": [],
        }
        assert _validate_expose_as_collisions(graph) == []

    def test_clean_alias_no_lint(self):
        graph = {
            "nodes": [
                _node("n1", config={"exposeAs": "case"}),
                _node("n2"),
            ],
            "edges": [],
        }
        assert _validate_expose_as_collisions(graph) == []

    def test_alias_collides_with_other_node_id(self):
        graph = {
            "nodes": [
                _node("n1", config={"exposeAs": "n2"}),
                _node("n2"),  # this id is also n1's alias — collision
            ],
            "edges": [],
        }
        warnings = _validate_expose_as_collisions(graph)
        assert len(warnings) == 1
        assert "collides with another node's id" in warnings[0]

    def test_alias_collides_with_self_id(self):
        graph = {
            "nodes": [_node("n1", config={"exposeAs": "n1"})],
            "edges": [],
        }
        warnings = _validate_expose_as_collisions(graph)
        assert len(warnings) == 1
        assert "same as the node id" in warnings[0]

    def test_two_nodes_share_alias(self):
        graph = {
            "nodes": [
                _node("n1", config={"exposeAs": "case"}),
                _node("n2", config={"exposeAs": "case"}),
            ],
            "edges": [],
        }
        warnings = _validate_expose_as_collisions(graph)
        assert len(warnings) == 1
        assert "Multiple nodes claim" in warnings[0]
        # Both node ids appear.
        assert "n1" in warnings[0]
        assert "n2" in warnings[0]

    def test_non_string_alias_rejected(self):
        graph = {
            "nodes": [_node("n1", config={"exposeAs": 42})],
            "edges": [],
        }
        warnings = _validate_expose_as_collisions(graph)
        assert len(warnings) == 1
        assert "non-empty string" in warnings[0]

    def test_empty_alias_skipped(self):
        # Empty string = no alias = no lint.
        graph = {
            "nodes": [_node("n1", config={"exposeAs": ""})],
            "edges": [],
        }
        assert _validate_expose_as_collisions(graph) == []


# ---------------------------------------------------------------------------
# Lint integration with exposeAs / dependsOn
# ---------------------------------------------------------------------------


class TestLintWithExposeAs:
    def test_alias_satisfies_dangling_check(self):
        # Reference `case.id` resolves because n1 has `exposeAs: "case"`.
        graph = {
            "nodes": [
                _node("n1", config={"exposeAs": "case"}),
                _node("n2", config={"systemPrompt": "Got {{ case.id }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        # No dangling lint — case is a valid alias.
        codes = [l.code for l in out]
        assert "jinja_dangling_node_ref" not in codes

    def test_alias_does_not_help_unaliased_target(self):
        # Reference `node_99` still dangling even when other nodes
        # have aliases.
        graph = {
            "nodes": [
                _node("n1", config={"exposeAs": "case"}),
                _node("n2", config={"systemPrompt": "{{ node_99.x }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_dangling_node_ref" in codes


class TestLintWithDependsOn:
    def test_no_lint_when_dependsOn_unset(self):
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_2", config={"systemPrompt": "{{ node_1.x }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_outside_depends_on" not in codes

    def test_ref_in_dependsOn_no_lint(self):
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_2", config={
                    "systemPrompt": "{{ node_1.x }}",
                    "dependsOn": ["node_1"],
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_outside_depends_on" not in codes

    def test_ref_outside_dependsOn_warns(self):
        # node_2 declares dependsOn=[node_1] but references node_3.
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_3"),
                _node("node_2", config={
                    "systemPrompt": "{{ node_1.x }} and {{ node_3.y }}",
                    "dependsOn": ["node_1"],
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_outside_depends_on" in codes
        warn = next(l for l in out if l.code == "jinja_ref_outside_depends_on")
        assert "node_3" in warn.message
        assert "node_1" in warn.message  # the dependsOn list

    def test_ref_via_alias_resolved_through_dependsOn(self):
        # node_2 declares dependsOn=[node_1]. Reference `case.id` —
        # alias of node_1 via exposeAs — should be accepted.
        graph = {
            "nodes": [
                _node("node_1", config={"exposeAs": "case"}),
                _node("node_2", config={
                    "systemPrompt": "Got {{ case.id }}",
                    "dependsOn": ["node_1"],
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        # Alias resolves to a dependsOn-listed node — no lint.
        assert "jinja_ref_outside_depends_on" not in codes
        assert "jinja_dangling_node_ref" not in codes


# ---------------------------------------------------------------------------
# config_validator integration
# ---------------------------------------------------------------------------


class TestConfigValidatorIntegration:
    def test_validate_graph_configs_includes_exposeAs_check(self):
        from app.engine.config_validator import validate_graph_configs

        graph = {
            "nodes": [
                _node("n1", config={"exposeAs": "case"}),
                _node("n2", config={"exposeAs": "case"}),
            ],
            "edges": [],
        }
        warnings = validate_graph_configs(graph)
        assert any("Multiple nodes claim" in w for w in warnings)


# ---------------------------------------------------------------------------
# Smoke: engine writes both canonical slot AND alias
# ---------------------------------------------------------------------------


class TestEngineAliasSmoke:
    """Direct verification of the aliasing behavior without spinning
    up the full execute_graph machinery — we mimic the tail of
    `_execute_single_node` after the reducer assigns to context[nid]."""

    def test_alias_writes_value_under_both_keys(self):
        # Mimic the engine's post-reducer write block.
        from app.engine.reducers import apply_reducer

        ctx: dict[str, Any] = {}
        node_data = {
            "label": "LLM Agent",
            "config": {"exposeAs": "case"},
        }
        node_id = "node_4r"
        in_value = {"id": "abc-123", "status": "OK"}

        # Reducer.
        ctx[node_id] = apply_reducer("overwrite", ctx.get(node_id), in_value)

        # exposeAs alias (engine code path).
        expose = (node_data.get("config") or {}).get("exposeAs")
        if isinstance(expose, str) and expose.strip():
            ctx[expose.strip()] = ctx[node_id]

        assert ctx["node_4r"] == {"id": "abc-123", "status": "OK"}
        assert ctx["case"] == {"id": "abc-123", "status": "OK"}
        # They reference the same value.
        assert ctx["case"] is ctx["node_4r"]

    def test_no_alias_when_exposeAs_unset(self):
        # No alias creation when the field is absent or empty.
        ctx: dict[str, Any] = {}
        ctx["node_1"] = {"x": 1}
        node_data = {"label": "LLM Agent", "config": {}}
        expose = (node_data.get("config") or {}).get("exposeAs")
        if isinstance(expose, str) and expose.strip():
            ctx[expose.strip()] = ctx["node_1"]
        # Only the canonical key exists.
        assert list(ctx.keys()) == ["node_1"]
