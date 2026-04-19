"""Pending S1-12 follow-up tests — scaffolded, not yet implemented.

These cover scenarios flagged in the Sprint 1 review that need a running
Postgres AND more elaborate setup (LLM stubbing, sub-workflow graph
construction). Stubbed here so the slots are tracked alongside the live
integration tests and don't silently disappear.

Remove the ``pytest.skip`` and fill in the body as each one is completed.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="S1-12 follow-up: HITL context_patch shallow-merge")
def test_hitl_resume_context_patch_shallow_merges_into_context(app_session):
    """Resume a suspended workflow with a context_patch and assert that
    the patch keys overwrite matching keys at the top level while other
    keys (including nested ones) are left untouched.

    Requires: a suspended WorkflowInstance + mocked downstream handlers
    that record the context they receive.
    """
    raise NotImplementedError


@pytest.mark.skip(reason="S1-12 follow-up: sub-workflow parent_instance_id cascade")
def test_sub_workflow_parent_instance_cascade(app_session):
    """Create a parent + child WorkflowInstance via _handle_sub_workflow;
    delete the parent; assert the child instance's parent_instance_id is
    set to NULL (per the ON DELETE SET NULL in the FK — or deleted, per
    the model definition, whichever is authoritative).

    Requires: WorkflowDefinition for both parent and child + mocked LLM
    provider so execute_graph doesn't actually call Google/OpenAI.
    """
    raise NotImplementedError


@pytest.mark.skip(reason="S1-12 follow-up: two Beat processes racing the same minute")
def test_two_beat_processes_cannot_both_fire_same_minute(app_session):
    """Spawn two threads that both invoke check_scheduled_workflows at
    the same minute boundary. Assert exactly one WorkflowInstance is
    created. Currently covered at the unit level by
    test_scheduled_trigger_dedupe but the end-to-end flow (cron check +
    claim + instance insert + task dispatch) deserves its own test.
    """
    raise NotImplementedError
