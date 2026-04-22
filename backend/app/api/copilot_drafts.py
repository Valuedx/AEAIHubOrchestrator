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
# Helpers
# ---------------------------------------------------------------------------


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
