"""COPILOT-01 — draft-workspace CRUD + tool dispatch + promote.

The safety boundary between a copilot's speculative edits and the live
workflow catalogue. Every mutation the copilot makes lands in a
``workflow_drafts`` row; nothing reaches ``workflow_definitions``
until the human calls ``/promote``.

Endpoint map
------------

``POST   /api/v1/copilot/drafts``                         create (optionally from base)
``GET    /api/v1/copilot/drafts``                         list drafts for tenant
``GET    /api/v1/copilot/drafts/{id}``                    read draft + last validation
``PATCH  /api/v1/copilot/drafts/{id}``                    manual graph / title update
``DELETE /api/v1/copilot/drafts/{id}``                    abandon
``POST   /api/v1/copilot/drafts/{id}/tools/{tool_name}``  dispatch a tool call
``POST   /api/v1/copilot/drafts/{id}/promote``            merge into workflow_definitions

Optimistic concurrency
----------------------

Mutating endpoints (``PATCH``, tool dispatch, ``/promote``) accept a
client-supplied ``expected_version`` in the body. If omitted we don't
check — the FE always sends it; omit-is-opt-out exists so CLI/ad-hoc
calls stay ergonomic. On mismatch we return 409 with the current
version so the caller refetches before retrying. Every successful
mutation bumps ``version`` by 1 and updates ``updated_at``.

Promote race
------------

The draft carries ``base_version_at_fork`` captured at create time. On
``/promote`` we refuse if ``base_workflow.version != base_version_at_fork``
— a colleague saved the base in another tab and blind-clobbering
their work is never the right call. The copilot flow for "base
diverged" is out of scope for 01a (planned: offer to fork a fresh
draft off the new base version, or a 3-way merge UI in 03+).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.copilot import tool_layer
from app.database import get_tenant_db
from app.models.copilot import WorkflowDraft
from app.models.workflow import WorkflowDefinition, WorkflowSnapshot
from app.security.tenant import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/copilot/drafts", tags=["copilot-drafts"])


Role = Literal["user", "assistant", "tool"]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DraftCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    # If set, the draft is seeded with this workflow's current graph_json
    # and ``base_version_at_fork`` is captured so /promote can refuse a
    # blind overwrite when the base has moved on.
    base_workflow_id: str | None = None


class DraftUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    graph_json: dict[str, Any] | None = None
    expected_version: int | None = None


class DraftOut(BaseModel):
    id: str
    tenant_id: str
    base_workflow_id: str | None
    base_version_at_fork: int | None
    title: str
    graph_json: dict[str, Any]
    version: int
    created_by: str | None
    created_at: str
    updated_at: str
    validation: dict[str, Any]


class ToolCallIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = None


class ToolCallOut(BaseModel):
    tool: str
    draft_version: int
    result: dict[str, Any]
    validation: dict[str, Any] | None = None


class PromoteIn(BaseModel):
    # Only meaningful for net-new promotes (no base_workflow_id). When
    # base is set we reuse the base workflow's current name.
    name: str | None = None
    description: str | None = None
    expected_version: int | None = None


class PromoteOut(BaseModel):
    workflow_id: str
    version: int
    created: bool  # True = net-new workflow; False = new version of existing


# ---------------------------------------------------------------------------
# COPILOT-03.e — scenario list + run-all endpoints
# ---------------------------------------------------------------------------


class ScenarioOut(BaseModel):
    scenario_id: str
    name: str
    payload: dict[str, Any]
    has_expected: bool
    created_at: str


class ScenarioRunOut(BaseModel):
    scenario_id: str
    name: str
    # "pass" | "fail" | "stale" | "error"
    status: str
    mismatches: list[dict[str, Any]] = Field(default_factory=list)
    actual_output: dict[str, Any] | None = None
    message: str | None = None


class ScenariosRunAllOut(BaseModel):
    count: int
    pass_count: int
    fail_count: int
    stale_count: int
    error_count: int
    results: list[ScenarioRunOut]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=DraftOut, status_code=201)
def create_draft(
    body: DraftCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    base_wf: WorkflowDefinition | None = None
    if body.base_workflow_id:
        try:
            base_id = uuid.UUID(body.base_workflow_id)
        except ValueError:
            raise HTTPException(422, "Invalid base_workflow_id")
        base_wf = (
            db.query(WorkflowDefinition)
            .filter_by(id=base_id, tenant_id=tenant_id)
            .first()
        )
        if base_wf is None:
            raise HTTPException(404, "Base workflow not found")

    draft = WorkflowDraft(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        base_workflow_id=base_wf.id if base_wf else None,
        base_version_at_fork=base_wf.version if base_wf else None,
        title=body.title,
        graph_json=(base_wf.graph_json if base_wf else {"nodes": [], "edges": []}),
        version=1,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _to_out(draft)


@router.get("", response_model=list[DraftOut])
def list_drafts(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    drafts = (
        db.query(WorkflowDraft)
        .filter_by(tenant_id=tenant_id)
        .order_by(WorkflowDraft.updated_at.desc())
        .all()
    )
    return [_to_out(d) for d in drafts]


@router.get("/{draft_id}", response_model=DraftOut)
def get_draft(
    draft_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    return _to_out(_get_or_404(db, tenant_id, draft_id))


@router.patch("/{draft_id}", response_model=DraftOut)
def update_draft(
    draft_id: str,
    body: DraftUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    draft = _get_or_404(db, tenant_id, draft_id)
    _check_version(draft, body.expected_version)

    if body.title is not None:
        draft.title = body.title
    if body.graph_json is not None:
        draft.graph_json = body.graph_json

    draft.version += 1
    db.commit()
    db.refresh(draft)
    return _to_out(draft)


@router.delete("/{draft_id}", status_code=204)
def delete_draft(
    draft_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    draft = _get_or_404(db, tenant_id, draft_id)
    db.delete(draft)
    db.commit()


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/tools/{tool_name}", response_model=ToolCallOut)
def call_tool(
    draft_id: str,
    tool_name: str,
    body: ToolCallIn,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Dispatch a pure tool-layer function against this draft's graph.

    For mutation tools the new graph is persisted inside the same
    transaction that bumps ``version``. For read-only tools nothing
    is written. See ``app/copilot/tool_layer.py`` for the tool list
    and argument shapes.
    """
    if tool_name not in tool_layer.TOOL_NAMES:
        raise HTTPException(
            400,
            f"Unknown tool '{tool_name}'. "
            f"Known tools: {sorted(tool_layer.TOOL_NAMES)}",
        )

    draft = _get_or_404(db, tenant_id, draft_id)
    _check_version(draft, body.expected_version)

    try:
        new_graph, result = tool_layer.dispatch(
            tool_name, draft.graph_json or {}, body.args or {},
        )
    except tool_layer.ToolLayerError as exc:
        raise HTTPException(400, str(exc))

    if tool_name in tool_layer.MUTATION_TOOLS:
        assert new_graph is not None  # dispatch guarantees this
        draft.graph_json = new_graph
        draft.version += 1
        validation = tool_layer.validate_graph(new_graph)
        db.commit()
        db.refresh(draft)
    else:
        validation = None

    return ToolCallOut(
        tool=tool_name,
        draft_version=draft.version,
        result=result,
        validation=validation,
    )


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


@router.post("/{draft_id}/promote", response_model=PromoteOut, status_code=201)
def promote_draft(
    draft_id: str,
    body: PromoteIn,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Atomically merge the draft into ``workflow_definitions``.

    Two paths:

    * **Net-new** (``base_workflow_id is None``) — insert a fresh
      ``WorkflowDefinition`` row at version 1 with the draft's graph.
      ``body.name`` is required for this path; an ``IntegrityError``
      on the tenant's existing index raises a 409 naming collision.

    * **New version of existing** (``base_workflow_id`` set) — append
      a ``WorkflowSnapshot`` of the base's current graph, bump
      ``version``, and overwrite ``graph_json``. Refuses with 409 if
      the base has moved past ``base_version_at_fork`` in the
      meantime (colleague saved a change in another tab).

    In both cases the draft is deleted on success.
    """
    draft = _get_or_404(db, tenant_id, draft_id)
    _check_version(draft, body.expected_version)

    # Validate before touching workflow_definitions — a promote that
    # lands an invalid graph is a worse UX than a validation 400.
    validation = tool_layer.validate_graph(draft.graph_json or {})
    if validation.get("errors"):
        raise HTTPException(
            400,
            f"Draft validation failed: {validation['errors']}",
        )

    # SMART-01 — strict promote-gate. Opt-in per tenant; when
    # enabled the promote endpoint runs every saved scenario first
    # and refuses with 400 on any non-pass result. No "promote
    # anyway" override in this mode — the PromoteDialog's soft gate
    # is replaced by a hard gate at the API layer.
    from app.engine.tenant_policy_resolver import get_effective_policy

    policy = get_effective_policy(tenant_id)
    if policy.smart_01_strict_promote_gate_enabled:
        _enforce_strict_scenario_gate(db, tenant_id, draft)

    if draft.base_workflow_id is None:
        workflow = _promote_as_new(db, tenant_id, draft, body)
        created = True
    else:
        workflow = _promote_as_new_version(db, tenant_id, draft, body)
        created = False

    # SMART-02 — persist the accepted graph + NL intent as a
    # learnable pattern before the draft (and its turn history) is
    # deleted below. save_accepted_pattern is best-effort — a save
    # failure is logged and swallowed so it can't block a promote.
    # It also reads the tenant's opt-out flag internally, so we
    # don't need to gate the call site on the policy.
    from app.copilot.pattern_library import save_accepted_pattern

    # Flush so the new workflow row has an id the pattern FK can
    # reference. Without this the FK insert fires before the
    # workflow insert has been flushed, and Postgres rejects it.
    db.flush()
    save_accepted_pattern(
        db,
        tenant_id=tenant_id,
        source_draft_id=draft.id,
        source_workflow_id=workflow.id,
        title=workflow.name,
        graph_json=workflow.graph_json or {"nodes": [], "edges": []},
    )

    # COPILOT-03.e — migrate test scenarios from draft_id →
    # workflow_id so regression cases survive the draft lifecycle.
    # Done INSIDE the transaction (before the draft delete below so
    # the FK cascade can't reach them) and via bulk UPDATE to keep
    # it O(1) round-trips regardless of scenario count.
    from app.models.copilot import CopilotTestScenario

    db.query(CopilotTestScenario).filter_by(
        tenant_id=tenant_id, draft_id=draft.id,
    ).update(
        {
            CopilotTestScenario.draft_id: None,
            CopilotTestScenario.workflow_id: workflow.id,
        },
        synchronize_session=False,
    )

    # Draft is consumed once promoted. Deleting it in the same
    # transaction keeps the invariant "a promoted draft no longer
    # exists" simple to reason about.
    db.delete(draft)
    db.commit()
    db.refresh(workflow)

    logger.info(
        "Copilot draft %s promoted %s workflow %s @ v%d (tenant=%s)",
        draft_id,
        "new" if created else "versioned",
        workflow.id,
        workflow.version,
        tenant_id,
    )
    return PromoteOut(
        workflow_id=str(workflow.id),
        version=workflow.version,
        created=created,
    )


# ---------------------------------------------------------------------------
# COPILOT-03.e — scenario list + run-all
# ---------------------------------------------------------------------------


@router.get("/{draft_id}/scenarios", response_model=list[ScenarioOut])
def list_draft_scenarios(
    draft_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """List saved regression scenarios bound to this draft. The
    PromoteDialog fetches this on open so it can show scenario names
    + status badges without running anything yet."""
    from app.models.copilot import CopilotTestScenario

    draft = _get_or_404(db, tenant_id, draft_id)
    rows = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .order_by(CopilotTestScenario.created_at)
        .all()
    )
    return [
        ScenarioOut(
            scenario_id=str(r.id),
            name=r.name,
            payload=r.payload_json or {},
            has_expected=r.expected_output_contains_json is not None,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


@router.post(
    "/{draft_id}/scenarios/run_all",
    response_model=ScenariosRunAllOut,
)
def run_all_draft_scenarios(
    draft_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Run every saved scenario on this draft sequentially and return
    pass/fail/stale/error counts + per-scenario results. The
    PromoteDialog calls this before showing the Apply button so the
    user sees regressions before promoting.

    Each scenario runs via the same ``run_scenario`` runner tool the
    agent uses — identical pass/fail semantics guaranteed by calling
    the same code path. Runs are sequential (not parallel) so the
    ephemeral-workflow rows don't step on each other and cost stays
    predictable.
    """
    from app.copilot import runner_tools
    from app.models.copilot import CopilotTestScenario

    draft = _get_or_404(db, tenant_id, draft_id)
    scenarios = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .order_by(CopilotTestScenario.created_at)
        .all()
    )

    results: list[ScenarioRunOut] = []
    counts = {"pass": 0, "fail": 0, "stale": 0, "error": 0}
    for s in scenarios:
        raw = runner_tools.run_scenario(
            db, tenant_id=tenant_id, draft=draft,
            args={"scenario_id": str(s.id)},
        )
        status = raw.get("status") or "error"
        if status not in counts:
            status = "error"
        counts[status] += 1
        results.append(
            ScenarioRunOut(
                scenario_id=str(s.id),
                name=s.name,
                status=status,
                mismatches=list(raw.get("mismatches") or []),
                actual_output=raw.get("actual_output"),
                message=raw.get("message") or raw.get("error"),
            )
        )

    return ScenariosRunAllOut(
        count=len(results),
        pass_count=counts["pass"],
        fail_count=counts["fail"],
        stale_count=counts["stale"],
        error_count=counts["error"],
        results=results,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enforce_strict_scenario_gate(
    db: Session,
    tenant_id: str,
    draft: WorkflowDraft,
) -> None:
    """SMART-01 strict gate. Runs every saved scenario on the draft
    and raises HTTP 400 on any non-pass result.

    No scenarios saved = no-op (you can't fail regressions you
    haven't written). That's intentional: strict mode is "if you've
    got tests, they must pass", not "you must have tests".
    """
    from app.copilot import runner_tools
    from app.models.copilot import CopilotTestScenario

    scenarios = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .order_by(CopilotTestScenario.created_at)
        .all()
    )
    if not scenarios:
        return

    failing: list[dict[str, Any]] = []
    for s in scenarios:
        raw = runner_tools.run_scenario(
            db, tenant_id=tenant_id, draft=draft,
            args={"scenario_id": str(s.id)},
        )
        if raw.get("status") != "pass":
            failing.append({
                "scenario_id": str(s.id),
                "name": s.name,
                "status": raw.get("status") or "error",
                "message": raw.get("message") or raw.get("error"),
                "mismatch_count": len(raw.get("mismatches") or []),
            })

    if failing:
        # 400 not 409 — this is a request-shape problem (the draft
        # fails its own regression tests). The client can retry after
        # either fixing the draft or disabling strict mode.
        raise HTTPException(
            400,
            {
                "error": "strict promote-gate blocked: scenarios failing",
                "failing_scenarios": failing,
                "hint": (
                    "SMART-01 strict promote-gate is enabled for this "
                    "tenant. Fix the failing scenarios, or disable the "
                    "flag via PATCH /api/v1/tenant-policy with "
                    "smart_01_strict_promote_gate_enabled=false."
                ),
            },
        )


def _get_or_404(db: Session, tenant_id: str, draft_id: str) -> WorkflowDraft:
    try:
        uid = uuid.UUID(draft_id)
    except ValueError:
        raise HTTPException(422, "Invalid draft_id")

    draft = (
        db.query(WorkflowDraft)
        .filter_by(id=uid, tenant_id=tenant_id)
        .first()
    )
    if draft is None:
        raise HTTPException(404, "Draft not found")
    return draft


def _check_version(draft: WorkflowDraft, expected: int | None) -> None:
    if expected is None:
        return
    if expected != draft.version:
        raise HTTPException(
            409,
            f"Draft version mismatch: expected {expected}, got {draft.version}. "
            "Refetch the draft before retrying.",
        )


def _promote_as_new(
    db: Session,
    tenant_id: str,
    draft: WorkflowDraft,
    body: PromoteIn,
) -> WorkflowDefinition:
    if not body.name:
        raise HTTPException(
            400,
            "Promoting a draft with no base_workflow_id requires 'name' "
            "in the promote body.",
        )

    # Name uniqueness within the tenant mirrors the workflow-save path
    # (see workflows.py IntegrityError catch).
    clash = (
        db.query(WorkflowDefinition)
        .filter_by(tenant_id=tenant_id, name=body.name)
        .first()
    )
    if clash is not None:
        raise HTTPException(409, f"Workflow '{body.name}' already exists")

    workflow = WorkflowDefinition(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        graph_json=draft.graph_json or {"nodes": [], "edges": []},
        version=1,
    )
    db.add(workflow)
    return workflow


def _promote_as_new_version(
    db: Session,
    tenant_id: str,
    draft: WorkflowDraft,
    body: PromoteIn,
) -> WorkflowDefinition:
    workflow = (
        db.query(WorkflowDefinition)
        .filter_by(id=draft.base_workflow_id, tenant_id=tenant_id)
        .first()
    )
    if workflow is None:
        raise HTTPException(
            404,
            "Base workflow has been deleted since this draft was created. "
            "Promote as a new workflow instead (create a copy).",
        )

    # Promote race check: the base must still be at the version we
    # forked from. Anything else means a colleague saved between draft
    # creation and promote, and silently clobbering their change is
    # exactly the bug this column exists to prevent.
    if (
        draft.base_version_at_fork is not None
        and workflow.version != draft.base_version_at_fork
    ):
        raise HTTPException(
            409,
            f"Base workflow has advanced from v{draft.base_version_at_fork} "
            f"to v{workflow.version} since this draft was forked. "
            "Discard or re-fork the draft against the current base.",
        )

    # Snapshot current version before overwriting — matches the
    # workflow-save path in api/workflows.py so rollback still works.
    snap = WorkflowSnapshot(
        workflow_def_id=workflow.id,
        tenant_id=tenant_id,
        version=workflow.version,
        graph_json=workflow.graph_json,
    )
    db.add(snap)

    workflow.graph_json = draft.graph_json or {"nodes": [], "edges": []}
    workflow.version += 1
    if body.description is not None:
        workflow.description = body.description

    return workflow


def _to_out(draft: WorkflowDraft) -> DraftOut:
    return DraftOut(
        id=str(draft.id),
        tenant_id=draft.tenant_id,
        base_workflow_id=str(draft.base_workflow_id) if draft.base_workflow_id else None,
        base_version_at_fork=draft.base_version_at_fork,
        title=draft.title,
        graph_json=draft.graph_json or {"nodes": [], "edges": []},
        version=draft.version,
        created_by=draft.created_by,
        created_at=draft.created_at.isoformat() if draft.created_at else "",
        updated_at=draft.updated_at.isoformat() if draft.updated_at else "",
        validation=tool_layer.validate_graph(draft.graph_json or {}),
    )
