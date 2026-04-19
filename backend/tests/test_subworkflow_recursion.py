"""Unit tests for the Sub-Workflow recursion guard.

These tests cover the pure check extracted from _handle_sub_workflow so we
don't need a live database or WorkflowDefinition fixtures to exercise the
cycle / max-depth logic.
"""

import pytest

from app.engine.node_handlers import check_subworkflow_recursion


class TestCycleDetection:
    def test_straight_call_ok(self):
        chain = check_subworkflow_recursion(
            parent_chain=[],
            current_wf_id="wf-parent",
            target_wf_id="wf-child",
            target_name="Child",
            max_depth=5,
        )
        assert chain == ["wf-parent"]

    def test_nested_call_ok(self):
        chain = check_subworkflow_recursion(
            parent_chain=["wf-root"],
            current_wf_id="wf-middle",
            target_wf_id="wf-leaf",
            target_name="Leaf",
            max_depth=5,
        )
        assert chain == ["wf-root", "wf-middle"]

    def test_self_call_rejected(self):
        with pytest.raises(ValueError, match="recursive cycle"):
            check_subworkflow_recursion(
                parent_chain=[],
                current_wf_id="wf-a",
                target_wf_id="wf-a",
                target_name="A",
                max_depth=5,
            )

    def test_transitive_cycle_rejected(self):
        # a -> b -> c -> a
        with pytest.raises(ValueError, match="recursive cycle"):
            check_subworkflow_recursion(
                parent_chain=["wf-a", "wf-b"],
                current_wf_id="wf-c",
                target_wf_id="wf-a",
                target_name="A",
                max_depth=10,
            )

    def test_missing_current_wf_id_still_walks_parent_chain(self):
        # Edge case: orphan context with no _workflow_def_id
        # parent_chain contains the target — still a cycle.
        with pytest.raises(ValueError, match="recursive cycle"):
            check_subworkflow_recursion(
                parent_chain=["wf-a"],
                current_wf_id="",
                target_wf_id="wf-a",
                target_name="A",
                max_depth=5,
            )


class TestDepthCap:
    def test_at_cap_rejected(self):
        # parent_chain of length 5 + current (6) hits max_depth=5
        with pytest.raises(ValueError, match="maximum nesting depth"):
            check_subworkflow_recursion(
                parent_chain=["w1", "w2", "w3", "w4"],
                current_wf_id="w5",
                target_wf_id="child",
                target_name="Child",
                max_depth=5,
            )

    def test_under_cap_ok(self):
        chain = check_subworkflow_recursion(
            parent_chain=["w1", "w2", "w3"],
            current_wf_id="w4",
            target_wf_id="child",
            target_name="Child",
            max_depth=5,
        )
        assert len(chain) == 4

    def test_depth_check_uses_full_chain_not_parent_only(self):
        # parent_chain len 2 + current = 3; max_depth=3 → reject
        with pytest.raises(ValueError, match="maximum nesting depth"):
            check_subworkflow_recursion(
                parent_chain=["w1", "w2"],
                current_wf_id="w3",
                target_wf_id="child",
                target_name="Child",
                max_depth=3,
            )


class TestReturnedChain:
    def test_returned_chain_does_not_include_target(self):
        # The target is the child being called; the returned chain is what
        # gets passed INTO the child as its new _parent_chain.
        chain = check_subworkflow_recursion(
            parent_chain=["root"],
            current_wf_id="mid",
            target_wf_id="child",
            target_name="Child",
            max_depth=10,
        )
        assert "child" not in chain
        assert chain == ["root", "mid"]

    def test_returned_chain_is_a_new_list(self):
        parent = ["root"]
        chain = check_subworkflow_recursion(
            parent_chain=parent,
            current_wf_id="mid",
            target_wf_id="child",
            target_name="Child",
            max_depth=10,
        )
        assert chain is not parent
        chain.append("mutation")
        assert parent == ["root"]
