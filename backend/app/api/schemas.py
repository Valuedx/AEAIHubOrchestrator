"""Pydantic request/response schemas for the orchestrator API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Workflow Definitions
# ---------------------------------------------------------------------------

class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    graph_json: dict[str, Any]


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    graph_json: dict[str, Any] | None = None
    # DV-07 — when False, Schedule Triggers stop firing. Not tied to
    # version: toggling is_active does not bump version or snapshot.
    is_active: bool | None = None


class WorkflowOut(BaseModel):
    id: uuid.UUID
    tenant_id: str
    name: str
    description: str | None
    graph_json: dict[str, Any]
    version: int
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Workflow Instances (Executions)
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    trigger_payload: dict[str, Any] | None = None
    deterministic_mode: bool = Field(
        False,
        description=(
            "When True, parallel node batches are submitted and logged in stable "
            "sorted node-ID order. Results are processed in submission order rather "
            "than completion order, giving fully reproducible execution logs. "
            "Slightly reduces throughput for large parallel batches; leave False "
            "for production hot-paths."
        ),
    )
    sync: bool = Field(
        False,
        description=(
            "When True, run the workflow inline on the API server (bypasses Celery) "
            "and return HTTP 200 with the final context when the run reaches a "
            "terminal status. Use for API-first callers that cannot poll or use SSE."
        ),
    )
    sync_timeout: int = Field(
        120,
        ge=5,
        le=3600,
        description="Seconds to wait for a synchronous run before returning 504 Gateway Timeout.",
    )


class SyncExecuteOut(BaseModel):
    """Response body for synchronous workflow execution (HTTP 200)."""

    instance_id: uuid.UUID
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    output: dict[str, Any] = Field(
        ...,
        description="Final execution context with internal (_-prefixed) keys stripped.",
    )


class CallbackRequest(BaseModel):
    approval_payload: dict[str, Any] = Field(default_factory=dict)
    context_patch: dict[str, Any] | None = Field(
        None,
        description=(
            "Optional shallow-merge patch applied to the workflow context before "
            "resuming. Keys in this dict overwrite matching keys in the existing "
            "context_json. Use to inject corrected values (e.g., a fixed node "
            "output) without rerunning the entire workflow from scratch."
        ),
    )


class InstanceContextOut(BaseModel):
    """Current snapshot of a suspended instance exposed for HITL review."""
    instance_id: uuid.UUID
    status: str
    current_node_id: str | None
    approval_message: str | None
    context_json: dict[str, Any]


class RetryRequest(BaseModel):
    """Retry a failed workflow instance from the failed node or a specific node."""
    from_node_id: str | None = Field(
        None, description="Optional node ID to retry from. Defaults to the node that failed."
    )


class ResumePausedRequest(BaseModel):
    """Resume a workflow instance that was paused between nodes."""

    context_patch: dict[str, Any] | None = Field(
        None,
        description=(
            "Optional shallow-merge patch applied to context before resuming "
            "(same semantics as HITL callback context_patch)."
        ),
    )


class InstanceOut(BaseModel):
    id: uuid.UUID
    tenant_id: str
    workflow_def_id: uuid.UUID
    status: str
    current_node_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    definition_version_at_start: int | None = Field(
        None,
        description=(
            "WorkflowDefinition.version when this instance was queued. "
            "Use with GET …/graph-at-version/{version} to restore the canvas for replay."
        ),
    )
    parent_instance_id: uuid.UUID | None = Field(
        None,
        description="ID of the parent instance if this is a sub-workflow execution.",
    )
    parent_node_id: str | None = Field(
        None,
        description="Node ID in the parent workflow that spawned this sub-workflow.",
    )
    suspended_reason: str | None = Field(
        None,
        description=(
            "When status='suspended', distinguishes HITL (None) from "
            "async-external systems like AutomationEdge ('async_external'). "
            "Frontend uses this to pick the right UI state."
        ),
    )

    model_config = {"from_attributes": True}


class ExecutionLogOut(BaseModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    node_id: str
    node_type: str
    status: str
    input_json: dict[str, Any] | None
    output_json: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class ChildInstanceSummary(BaseModel):
    """Summary of a child sub-workflow instance shown in parent detail."""
    id: uuid.UUID
    workflow_def_id: uuid.UUID
    workflow_name: str | None = None
    parent_node_id: str | None = None
    status: str
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class AsyncJobOut(BaseModel):
    """Outstanding (or recently-finalised) async-external job on a
    workflow instance. Surfaced to the UI so the 'waiting-on-external'
    badge can show the system, elapsed time, and Diverted sub-state.

    Secret-bearing fields (webhook_token, webhook_hmac_secret) never
    leave the server — only the handful of public metadata fields are
    returned so the UI can render the badge without exposing
    credentials.
    """
    id: uuid.UUID
    instance_id: uuid.UUID
    node_id: str
    system: str
    external_job_id: str
    status: str
    submitted_at: datetime
    last_polled_at: datetime | None
    completed_at: datetime | None
    last_external_status: str | None = None
    total_diverted_ms: int = 0
    diverted_since: datetime | None = None
    last_error: str | None = None

    model_config = {"from_attributes": True}


class InstanceDetailOut(InstanceOut):
    logs: list[ExecutionLogOut] = []
    children: list[ChildInstanceSummary] = Field(
        default_factory=list,
        description="Child sub-workflow instances spawned by this execution.",
    )


# ---------------------------------------------------------------------------
# Workflow Snapshots (version history)
# ---------------------------------------------------------------------------

class SnapshotOut(BaseModel):
    id: uuid.UUID
    workflow_def_id: uuid.UUID
    version: int
    saved_at: datetime | None

    model_config = {"from_attributes": True}


class SnapshotDetailOut(SnapshotOut):
    graph_json: dict[str, Any]


class GraphAtVersionOut(BaseModel):
    """Graph JSON for a historical definition version (or the live definition if it matches)."""

    version: int
    graph_json: dict[str, Any]


# ---------------------------------------------------------------------------
# Instance Checkpoints (per-node context snapshots)
# ---------------------------------------------------------------------------

class CheckpointOut(BaseModel):
    """Summary of a single per-node checkpoint (no context payload)."""
    id: uuid.UUID
    instance_id: uuid.UUID
    node_id: str
    saved_at: datetime | None

    model_config = {"from_attributes": True}


class CheckpointDetailOut(CheckpointOut):
    """Full checkpoint including the context snapshot at that point."""
    context_json: dict[str, Any]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class ToolOut(BaseModel):
    name: str
    title: str
    description: str
    category: str
    safety_tier: str
    tags: list[str] = []


# ---------------------------------------------------------------------------
# Conversation Sessions (Stateful Re-Trigger Pattern)
# ---------------------------------------------------------------------------

class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: str | None = None


class ConversationSessionOut(BaseModel):
    session_id: str
    tenant_id: str
    messages: list[ConversationMessage]
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationSessionSummary(BaseModel):
    session_id: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationEpisodeOut(BaseModel):
    id: uuid.UUID
    session_id: str
    status: str
    start_turn: int
    end_turn: int | None
    title: str | None
    checkpoint_summary_text: str | None
    summary_through_turn: int
    archive_reason: str | None
    last_activity_at: datetime
    archived_at: datetime | None
    archived_memory_record_id: uuid.UUID | None
    # The migration declares these nullable=True; use Optional to match the DB contract.
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class ArchiveConversationEpisodeRequest(BaseModel):
    reason: str = Field(default="manual", pattern="^(resolved|inactive|manual)$")
    summary_text: str | None = None
    title: str | None = None
    memory_profile_id: uuid.UUID | None = None


class ArchiveConversationEpisodeOut(BaseModel):
    session_id: str
    archived: bool
    episode_id: uuid.UUID | None
    title: str | None
    archive_reason: str | None
    archived_at: datetime | None
    memory_record_ids: list[uuid.UUID]
    memory_records_created: int
    summary_text: str


# ---------------------------------------------------------------------------
# A2A Protocol (Agent-to-Agent)
# ---------------------------------------------------------------------------

class A2AMessagePart(BaseModel):
    """A single content unit inside an A2A message (text or future media types)."""
    text: str | None = None


class A2AMessage(BaseModel):
    role: str = "user"
    parts: list[A2AMessagePart]


class A2ATaskStatus(BaseModel):
    # A2A spec states: submitted | working | input-required | completed | failed | canceled
    state: str
    message: A2AMessage | None = None
    timestamp: str | None = None


class A2AArtifact(BaseModel):
    """Final output of a completed task — one or more content parts."""
    index: int = 0
    parts: list[A2AMessagePart]
    lastChunk: bool | None = None


class A2ATask(BaseModel):
    """Full A2A Task object returned by tasks/send and tasks/get."""
    id: str
    sessionId: str | None = None
    status: A2ATaskStatus
    artifacts: list[A2AArtifact] = []


class A2ASendParams(BaseModel):
    """Parameters for the tasks/send JSON-RPC method."""
    id: str | None = None          # caller-supplied idempotency key
    sessionId: str | None = None   # conversation thread
    skillId: str                   # workflow_def_id (UUID string)
    message: A2AMessage


class A2AJsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = {}


class A2AJsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# A2A API key management (tenant-facing REST endpoints, not part of the A2A spec)

class A2AApiKeyCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=128,
                       description="Human-readable name for this key, e.g. 'teams-bot'")


class A2AApiKeyCreated(BaseModel):
    """Returned once at creation time. raw_key is never stored."""
    id: uuid.UUID
    label: str
    raw_key: str
    created_at: datetime

    model_config = {"from_attributes": True}


class A2AApiKeyOut(BaseModel):
    """Safe summary — no key material."""
    id: uuid.UUID
    label: str
    created_at: datetime

    model_config = {"from_attributes": True}


# A2A publish/unpublish workflow

class WorkflowPublishRequest(BaseModel):
    is_published: bool
