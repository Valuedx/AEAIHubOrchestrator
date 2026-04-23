"""NODES-01.a + NODES-01.b — Switch (multi-branch) + While handler tests.

Covers:

* Switch: match, case-insensitive match, default fallback, unknown-branch
  safety, expression-error safety, non-string value coercion.
* While: maps condition → continueExpression + maxIterations clamp.
* dag_runner: Switch participates in branch-pruning the same way
  Condition does (any non-matching ``sourceHandle`` is pruned).
"""

from __future__ import annotations

import pytest

from app.engine.node_handlers import _handle_switch, _handle_while


# ---------------------------------------------------------------------------
# Switch handler
# ---------------------------------------------------------------------------


def _ctx(**kwargs):
    return {"trigger": {}, **kwargs}


def _switch_node(expression, cases, *, match_mode="equals", default_label="Default"):
    return {
        "data": {"nodeCategory": "logic", "label": "Switch"},
        "config": {
            "expression": expression,
            "cases": cases,
            "matchMode": match_mode,
            "defaultLabel": default_label,
        },
    }


def test_switch_matches_first_case():
    node = _switch_node(
        "node_2.intent",
        cases=[
            {"value": "diagnose", "label": "Diagnose"},
            {"value": "remediate", "label": "Remediate"},
        ],
    )
    ctx = _ctx(node_2={"intent": "diagnose"})
    out = _handle_switch(node, ctx, "tenant")
    assert out["branch"] == "diagnose"
    assert out["evaluated"] == "diagnose"
    assert out["match"] == "case"


def test_switch_routes_to_default_on_no_match():
    node = _switch_node(
        "node_2.intent",
        cases=[{"value": "diagnose", "label": "Diagnose"}],
    )
    ctx = _ctx(node_2={"intent": "nope"})
    out = _handle_switch(node, ctx, "tenant")
    assert out["branch"] == "default"
    assert out["match"] == "default"


def test_switch_case_insensitive_match():
    node = _switch_node(
        "node_2.intent",
        cases=[{"value": "Refund", "label": "Refund"}],
        match_mode="equals_ci",
    )
    ctx = _ctx(node_2={"intent": "refund"})
    out = _handle_switch(node, ctx, "tenant")
    assert out["branch"] == "Refund"  # preserves the original case value


def test_switch_case_sensitive_miss_with_plain_equals():
    node = _switch_node(
        "node_2.intent",
        cases=[{"value": "Refund", "label": "Refund"}],
    )
    ctx = _ctx(node_2={"intent": "refund"})
    out = _handle_switch(node, ctx, "tenant")
    assert out["branch"] == "default"


def test_switch_empty_expression_routes_to_default():
    node = _switch_node("", cases=[{"value": "a", "label": "A"}])
    out = _handle_switch(node, _ctx(), "tenant")
    assert out["branch"] == "default"


def test_switch_coerces_non_string_values():
    """Authors shouldn't have to quote int/bool in case values."""
    node = _switch_node(
        "node_2.priority",
        cases=[{"value": "1", "label": "P1"}, {"value": "2", "label": "P2"}],
    )
    ctx = _ctx(node_2={"priority": 1})
    out = _handle_switch(node, ctx, "tenant")
    assert out["branch"] == "1"


def test_switch_expression_error_degrades_to_default():
    # Reference to an undefined variable should degrade gracefully.
    node = _switch_node(
        "node_999.missing",  # node_999 doesn't exist in context
        cases=[{"value": "a", "label": "A"}],
    )
    out = _handle_switch(node, _ctx(), "tenant")
    assert out["branch"] == "default"


def test_switch_empty_cases_always_default():
    node = _switch_node("node_2.x", cases=[])
    out = _handle_switch(node, _ctx(node_2={"x": "anything"}), "tenant")
    assert out["branch"] == "default"


def test_switch_skips_non_dict_case_entries():
    """A malformed graph_json mustn't crash the handler."""
    node = _switch_node(
        "node_2.x",
        cases=[
            None,  # type: ignore
            {"value": "ok", "label": "OK"},
        ],
    )
    out = _handle_switch(node, _ctx(node_2={"x": "ok"}), "tenant")
    assert out["branch"] == "ok"


# ---------------------------------------------------------------------------
# While handler
# ---------------------------------------------------------------------------


def test_while_maps_condition_to_continue_expression():
    node = {
        "data": {"nodeCategory": "logic", "label": "While"},
        "config": {"condition": "node_3.score < 0.9", "maxIterations": 5},
    }
    out = _handle_while(node, _ctx(), "tenant")
    assert out["continueExpression"] == "node_3.score < 0.9"
    assert out["maxIterations"] == 5


def test_while_clamps_to_hard_cap_25():
    node = {
        "data": {"nodeCategory": "logic", "label": "While"},
        "config": {"condition": "true", "maxIterations": 999},
    }
    out = _handle_while(node, _ctx(), "tenant")
    assert out["maxIterations"] == 25


def test_while_accepts_empty_condition_degrades_to_loop_semantics():
    """Empty condition is caught by save-time validation, but the
    handler mustn't raise — it just returns empty continueExpression
    so the existing Loop runner handles it like Loop would."""
    node = {
        "data": {"nodeCategory": "logic", "label": "While"},
        "config": {"condition": "", "maxIterations": 3},
    }
    out = _handle_while(node, _ctx(), "tenant")
    assert out["continueExpression"] == ""
    assert out["maxIterations"] == 3


# ---------------------------------------------------------------------------
# dag_runner branch pruning — Switch parity with Condition
# ---------------------------------------------------------------------------


def test_dag_runner_treats_switch_as_branch_node():
    """Source code smoke test: the generalised ``is_branch_node``
    check must include Switch so prune_subtree fires for unmatched
    handles."""
    # Read the dag_runner source and pin the check.
    from pathlib import Path

    dag_runner_src = Path(__file__).resolve().parents[1] / "app" / "engine" / "dag_runner.py"
    text = dag_runner_src.read_text(encoding="utf-8")
    # Both branch-gating call sites must include Switch.
    assert text.count('in {"Condition", "Switch"}') >= 2, (
        "dag_runner's is_branch_node check should include Switch in both "
        "_update_after_node_completion and _should_fire_edge"
    )
