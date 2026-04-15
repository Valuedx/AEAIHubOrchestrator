---
name: P0 P2 execution plan
overview: Detailed incremental execution plan for ContextEdge MVP hardening and post-P1 gap remediation, with progress updated through April 12, 2026 for branch codex/p0-plan-implementation.
todos:
  - id: p0a
    content: "P0a: Security hardening - config.py validator, provider.py debug removal + JSON error handling, patterns.py RBAC, playbooks.py RBAC + negative knowledge"
    status: completed
  - id: p0b
    content: "P0b: Async episode reconstruction - episodes.py inline _reconstruct -> Celery .delay()"
    status: completed
  - id: p0c
    content: "P0c: Evidence search fix - wire query param to search_evidence_fts in evidence.py"
    status: completed
  - id: p0d
    content: "P0d: Retention legal-hold bug - add legal_hold exclusion in retention_service.py"
    status: completed
  - id: p0e
    content: "P0e: Test suite for all P0 items - conftest fixtures, 4 new test files, 8+ test functions"
    status: completed
  - id: p1a
    content: "P1a: FTS GIN indexes - Alembic 0007, stored tsvector columns, update pg_fts.py + evidence model"
    status: completed
  - id: p1b
    content: "P1b: Decision traces + sessions - Alembic 0008, models/session.py, services/session_service.py, api/v1/sessions.py, runtime.py session_id"
    status: completed
  - id: p1c
    content: "P1c: Object store wiring - services/object_store.py, ingestion_persistence.py offload, main.py bucket init, extraction_tasks.py offload read"
    status: completed
  - id: p1d
    content: "P1d: Confidence separation - hybrid_ranker.py separate fields, RuntimeMatchResult schema, runtime.py populate"
    status: completed
  - id: p1e
    content: "P1e: Correlation worker - Alembic 0009 case_links, correlation_tasks.py, chain from normalize_evidence"
    status: completed
  - id: p1f
    content: "P1f: Contradiction detection - contradiction_service.py, Celery beat task, graph builder contradicts edge"
    status: completed
  - id: p1g
    content: "P1g: Access-aware retrieval - vector_search.py policy filter, hybrid_ranker.py policy passthrough, pg_fts.py filter"
    status: completed
  - id: p2a
    content: "P2a: Governed execution + action safety classes - execution runs, step/tool invocation ledger, approval-gated actions, runtime-to-execution handoff"
    status: completed
  - id: p2b
    content: "P2b: Explicit state clock vs event clock - append-only operational events, causation/correlation ids, state projections"
    status: completed
  - id: p2c
    content: "P2c: Identity resolution activation - wire canonical identities into normalization, correlation, retrieval, and graph links"
    status: completed
  - id: p2d
    content: "P2d: Explicit memory lifecycle separation - formal short-term, long-term, and reasoning memory services and promotion rules"
    status: completed
  - id: p2e
    content: "P2e: Attachment/log extraction pipeline - artifact ingestion, text extraction, provenance, and evidence linkage"
    status: completed
  - id: p2f
    content: "P2f: Workspace/team boundary controls - enforce workspace/domain ABAC across retrieval, runtime, and APIs"
    status: pending
  - id: p2g
    content: "P2g: Ingestion control plane + storage lifecycle - backlog visibility, retry/dead-letter controls, hot/warm/cold retention strategy"
    status: pending
isProject: false
---

# ContextEdge execution plan

## Progress update (April 12, 2026)

- Filename note: this document still lives at `p0_p1_execution_plan_bc2da78f.plan.md` for continuity with existing references, even though the active scope now includes `P2`.
- Implemented on branch: `codex/p0-plan-implementation`
- Completed in code and tests: `P0a`, `P0b`, `P0c`, `P0d`, `P0e`, `P1a`, `P1b`, `P1c`, `P1d`, `P1e`, `P1f`, `P1g`, `P2a`, `P2b`, `P2c`, `P2d`, `P2e`
- Pending implementation in the follow-on hardening plan: `P2f`, `P2g`
- Backend verification status: `pytest backend/tests` passed with 72 tests
- Migration status: migrations `0007` through `0013_attachment_processing` created in the branch; `0007_fts_gin_indexes` replaces placeholder FTS artifacts and should be coordinated during deploy; none were applied locally

## Global rollout order

```
P0a  Security hardening          (no migration, code-only)
P0b  Async episode reconstruct   (no migration, code-only)
P0c  Evidence search fix         (no migration, code-only)
P0d  Retention legal-hold bug    (no migration, code-only)
P0e  Tests for P0a-d             (no migration, test-only)
--- deploy P0 ---
P1a  FTS GIN indexes             (coordinated schema migration, perf)
P1b  Decision traces + sessions  (additive migration, new module)
P1c  Object store wiring         (no migration, new service)
P1d  Confidence separation       (no migration, schema + API)
P1e  Correlation worker          (additive migration, new task)
P1f  Contradiction detection     (no migration, new task)
P1g  Access-aware retrieval      (no migration, logic change)
--- deploy P1 ---
```

All P0 items are **code-only** with zero migrations, zero new tables, and zero breaking API changes. They can be deployed together as one release. All P1 items are **API-backward-compatible** and mostly additive, but `P1a` includes destructive DDL on placeholder FTS columns (`search_vector` / old FTS artifacts). Coordinate that migration with the checked-in code path and do not run old/new FTS code in parallel during deploy.

---

## P0a: Security hardening

Implementation note: function, route, and module names are the canonical references in this document. Any line numbers mentioned in older notes should be treated as implementation-time hints only and may drift as the code evolves.

### Files to change


| File                                                                                         | Change                                                           |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `[backend/src/contextedge/config.py](backend/src/contextedge/config.py)`                     | Add startup validator rejecting default JWT secret in non-dev    |
| `[backend/src/contextedge/ai/provider.py](backend/src/contextedge/ai/provider.py)`           | Replace `print(f"DEBUG: ...")` with structlog, wrap `json.loads` |
| `[backend/src/contextedge/api/v1/patterns.py](backend/src/contextedge/api/v1/patterns.py)`   | Uncomment RBAC, remove debug prints                              |
| `[backend/src/contextedge/api/v1/playbooks.py](backend/src/contextedge/api/v1/playbooks.py)` | Uncomment RBAC, load negative knowledge, remove debug prints     |


### Exact changes

**config.py** -- add after `settings = Settings()`:

```python
if settings.app_env != "development" and settings.jwt_secret_key == "change-me-in-production":
    raise RuntimeError(
        "JWT_SECRET_KEY must be changed from the default value in non-development environments. "
        "Set JWT_SECRET_KEY in your .env or environment variables."
    )
```

**provider.py** lines 14-27 -- replace 3 `print()` calls with:

```python
logger = structlog.get_logger()

if settings.google_api_key:
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    logger.debug("google_api_key_configured")

if settings.google_application_credentials:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
    os.environ["VERTEX_LOCATION"] = settings.location
    logger.debug("vertex_ai_credentials_configured", path=settings.google_application_credentials)
else:
    logger.debug("vertex_ai_credentials_not_found")
```

**provider.py** `llm_complete_json` -- wrap the return:

```python
async def llm_complete_json(
    prompt: str,
    task: str = "extraction",
    model: str | None = None,
    temperature: float = 0.0,
) -> dict | list:
    result = await llm_complete(
        prompt, task=task, model=model, temperature=temperature,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(result)
    except json.JSONDecodeError as exc:
        logger.error("llm_json_parse_failed", task=task, model=model or get_model_for_task(task), raw=result[:500])
        raise ValueError(f"LLM returned invalid JSON for task '{task}'") from exc
```

**patterns.py** -- in `discover_pattern`, enforce the existing RBAC guard:

```python
user.require_role("domain_admin")
```

Remove the temporary `print(...)` / traceback debug statements in the pattern discovery flow after restoring the RBAC guard.

**playbooks.py** -- in `generate_playbook`, enforce the knowledge-manager RBAC guard:

```python
user.require_role("knowledge_manager")
```

Replace the placeholder empty negative-knowledge list with the real query used in `[pattern_tasks.py](backend/src/contextedge/workers/pattern_tasks.py)`:

```python
from contextedge.models.pattern import NegativeKnowledgeItem
nk_r = await db.execute(
    select(NegativeKnowledgeItem).where(
        NegativeKnowledgeItem.tenant_id == user.tenant_id,
        NegativeKnowledgeItem.domain_id == pattern.domain_id,
    ).limit(20)
)
negative_knowledge = [
    f"{row.step_text} ({row.failure_reason or 'no reason'})"
    for row in nk_r.scalars().all()
]
```

Remove lines 258-261 (bare traceback.print_exc and raw exception detail in 500 response). Replace with structured logging.

### Schema migrations

None.

### API/interface changes

- `POST /patterns/discover` now returns **403** for users without `domain_admin` role (was open).
- `POST /playbooks/generate` now returns **403** for users without `knowledge_manager` role (was open).
- `POST /playbooks/generate` now includes negative knowledge in LLM prompt (better output quality, same schema).

### Worker/job changes

None.

### Test cases to add

- `test_patterns_rbac`: mock user without `domain_admin` role, call `discover_pattern`, assert 403.
- `test_playbooks_generate_rbac`: mock user without `knowledge_manager` role, call `generate_playbook`, assert 403.
- `test_llm_json_parse_error`: mock `llm_complete` returning invalid JSON, assert `ValueError` raised with structured message.

### Safe for current MVP?

Yes. The RBAC change is **intentionally breaking** for unauthorized callers (which should not have existed in production). The JSON error handling and debug print removal are invisible to API consumers.

### Rollback

Revert the commit. No data or schema changes to undo.

---

## P0b: Async episode reconstruction

### Files to change


| File                                                                                       | Change                                               |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------- |
| `[backend/src/contextedge/api/v1/episodes.py](backend/src/contextedge/api/v1/episodes.py)` | Replace inline `_reconstruct` with Celery `.delay()` |


### Exact changes

Lines 146-152 -- replace:

```python
from contextedge.workers.extraction_tasks import _reconstruct
cluster_id = ",".join([str(eid) for eid in evidence_ids])
await _reconstruct(db, cluster_id, user.tenant_id)
await db.commit()
```

with:

```python
from contextedge.workers.extraction_tasks import reconstruct_episode_task
cluster_id = ",".join([str(eid) for eid in evidence_ids])
task = reconstruct_episode_task.delay(cluster_id, str(user.tenant_id))
```

The Celery task `reconstruct_episode_task` already exists at `[extraction_tasks.py](backend/src/contextedge/workers/extraction_tasks.py)` line 198-213 with `name="extraction.reconstruct_episode"` and queue routing to `extraction`.

Update the response body (line 165-169) to include `task_id`:

```python
return {
    "status": "reconstruction_queued",
    "evidence_count": len(evidence_ids),
    "task_id": task.id,
}
```

Move the `log_audit_event` call before the task dispatch (lines 154-163) so it fires regardless of Celery availability.

### Schema migrations

None.

### API/interface changes

- `POST /episodes/reconstruct` response changes: adds `task_id` field, removes `correlation_id` field.
- Response is now truly **202 Accepted** (already declared) -- the work is no longer done when the response returns.

### Worker/job changes

None -- `reconstruct_episode_task` already exists and is registered on the `extraction` queue.

### Test cases to add

- `test_reconstruct_dispatches_celery`: mock `reconstruct_episode_task.delay`, call endpoint, assert `.delay` called with correct args, assert response has `task_id`.
- `test_reconstruct_requires_domain_admin`: mock user without role, assert 403. (Already enforced, just confirming with a test.)

### Safe for current MVP?

Yes. The endpoint already declares 202. Callers should already expect async behavior. The response field change (`correlation_id` to `task_id`) is minor -- if the frontend uses it, update `[frontend/src/app/(dashboard)/episodes/page.tsx](frontend/src/app/(dashboard)/episodes/page.tsx)`.

### Rollback

Revert to inline `_reconstruct`. No data changes.

---

## P0c: Evidence search fix

### Files to change


| File                                                                                       | Change                    |
| ------------------------------------------------------------------------------------------ | ------------------------- |
| `[backend/src/contextedge/api/v1/evidence.py](backend/src/contextedge/api/v1/evidence.py)` | Wire `query` param to FTS |


### Exact changes

In `search_evidence` (lines 19-42), add FTS branch when `query` is provided:

```python
@router.get("", response_model=list[EvidenceItemResponse])
async def search_evidence(
    db: DbSession,
    user: AuthUser,
    query: str | None = None,
    source_id: UUID | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    domain_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if query and query.strip():
        from contextedge.search.pg_fts import search_evidence_fts
        fts_results = await search_evidence_fts(db, user.tenant_id, query.strip(), limit=limit)
        return [item for item, _rank in fts_results]

    q = select(EvidenceItem).where(EvidenceItem.tenant_id == user.tenant_id)
    # ... rest unchanged
```

Note: `search_evidence_fts` in `[pg_fts.py](backend/src/contextedge/search/pg_fts.py)` already accepts `tenant_id` and `query`, and returns `list[tuple[EvidenceItem, rank]]`. We extract just the items.

The FTS path does not currently support the `source_id`/`relevance_state`/`evidence_type`/`domain_id` filters. For P0 this is acceptable (FTS overrides filters). For P1a (FTS indexes), we refactor to combine both.

### Schema migrations

None.

### API/interface changes

- `GET /evidence?query=...` now returns FTS-ranked results instead of ignoring the param. **Behavioral fix, same schema.**

### Worker/job changes

None.

### Test cases to add

- `test_evidence_search_with_query`: mock `search_evidence_fts` return value, call `GET /evidence?query=foo`, assert results match FTS output order.
- `test_evidence_search_without_query`: call `GET /evidence` with no query, assert existing filter path runs.

### Safe for current MVP?

Yes. Previously `query` was accepted but ignored -- callers already sending it will now get correct results. Callers not sending it see no change.

### Rollback

Revert to ignoring `query`. No data changes.

---

## P0d: Retention legal-hold bug

### Files to change


| File                                                                                                             | Change                                      |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| `[backend/src/contextedge/services/retention_service.py](backend/src/contextedge/services/retention_service.py)` | Add legal_hold exclusion to retention query |


### Exact changes

Line 31-34 -- add filter after the `where` clause:

```python
from sqlalchemy import or_

q = select(EvidenceItem).where(
    EvidenceItem.tenant_id == tenant_id,
    EvidenceItem.ingested_at < cutoff,
    or_(
        EvidenceItem.sensitivity_label.is_(None),
        EvidenceItem.sensitivity_label != "legal_hold",
    ),
)
```

### Schema migrations

None.

### API/interface changes

None.

### Test cases to add

- `test_retention_skips_legal_hold`: create two evidence items (one with `sensitivity_label="legal_hold"`, one without), run `apply_retention_policy`, assert only the non-held item is archived.

### Safe for current MVP?

Yes. This is a **bug fix** -- previously legal-hold items were incorrectly archived.

### Rollback

Revert filter. Items already archived during the bug window would need manual review (check `audit_logs` for retention events).

---

## P0e: Test infrastructure + P0 test suite

### Files to change/create


| File                                             | Change                                                         |
| ------------------------------------------------ | -------------------------------------------------------------- |
| `backend/tests/conftest.py`                      | Add mock fixtures for `CurrentUser`, `DbSession`, Celery tasks |
| `backend/tests/test_p0_security.py` (new)        | RBAC + JSON parse + config validation tests                    |
| `backend/tests/test_p0_evidence_search.py` (new) | Evidence search FTS wiring tests                               |
| `backend/tests/test_p0_retention.py` (new)       | Legal-hold exclusion test                                      |
| `backend/tests/test_p0_episodes.py` (new)        | Async reconstruction dispatch test                             |


### Fixture additions to conftest.py

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

def make_user(roles=None, tenant_id=None, principal_type="user"):
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        email="test@example.com",
        roles=roles or [],
        workspace_ids=[],
        principal_type=principal_type,
        allowed_domain_ids=None,
        has_role=lambda role: role in (roles or []) or "platform_super_admin" in (roles or []),
        require_role=lambda role: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail=f"Role '{role}' required")
        ) if role not in (roles or []) and "platform_super_admin" not in (roles or []) else None,
    )
```

### Test count: 8 new test functions across 4 files

### Rollout

Ship with the P0 code changes. Tests must pass in CI before merge.

---

## P1a: FTS GIN indexes (stored tsvector columns)

### Files to change


| File                                                                                       | Change                                                                                                               |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `backend/alembic/versions/0007_fts_gin_indexes.py` (new)                                   | Add stored tsvector columns + GIN indexes                                                                            |
| `[backend/src/contextedge/search/pg_fts.py](backend/src/contextedge/search/pg_fts.py)`     | Query against stored columns instead of computing on-the-fly                                                         |
| `[backend/src/contextedge/models/evidence.py](backend/src/contextedge/models/evidence.py)` | Declare the `search_tsvector` column and remove the placeholder `search_vector = mapped_column(Text, nullable=True)` |


### Schema migration (Alembic 0007)

Canonical source of truth: use the checked-in migration file `backend/alembic/versions/0007_fts_gin_indexes.py`. The snippet below is explanatory only and must not be copy-pasted as a fresh Alembic revision header.

```python
"""Add stored tsvector columns and GIN indexes for FTS."""

revision = "0007_fts_gin_indexes"
down_revision = "a4ccd43dcf94"

from alembic import op

def upgrade():
    # evidence_items: drop old placeholder, add generated tsvector
    op.execute("""
        ALTER TABLE evidence_items DROP COLUMN IF EXISTS search_vector;
        ALTER TABLE evidence_items ADD COLUMN search_tsvector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body_text, ''))
            ) STORED;
        CREATE INDEX ix_evidence_items_fts ON evidence_items USING GIN(search_tsvector);
    """)

    # playbooks
    op.execute("""
        ALTER TABLE playbooks ADD COLUMN search_tsvector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))
            ) STORED;
        CREATE INDEX ix_playbooks_fts ON playbooks USING GIN(search_tsvector);
    """)

def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_evidence_items_fts;")
    op.execute("ALTER TABLE evidence_items DROP COLUMN IF EXISTS search_tsvector;")
    op.execute("ALTER TABLE evidence_items ADD COLUMN search_vector TEXT;")
    op.execute("DROP INDEX IF EXISTS ix_playbooks_fts;")
    op.execute("ALTER TABLE playbooks DROP COLUMN IF EXISTS search_tsvector;")
```

### pg_fts.py changes

Replace runtime `func.to_tsvector(...)` with column reference:

```python
async def search_evidence_fts(db, tenant_id, query, limit=50):
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank(EvidenceItem.search_tsvector, tsquery)
    stmt = (
        select(EvidenceItem, rank.label("rank"))
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.search_tsvector.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()
```

Same pattern for `search_playbooks_fts`.

### evidence.py model change

Replace the placeholder `search_vector = mapped_column(Text, nullable=True)` declaration with:

```python
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
search_tsvector = mapped_column(TSVECTOR, nullable=True)
```

(The column is `GENERATED ALWAYS` in Postgres, so SQLAlchemy should not write to it. Mark it as `server_default` or use `Computed`.)

### API/interface changes

None (FTS already works; this is a performance improvement).

### Worker/job changes

None.

### Test cases to add

- `test_fts_uses_stored_column`: unit test that the `search_evidence_fts` query references `search_tsvector` not `to_tsvector(...)`.

### Safe for current MVP?

Yes. The migration is additive. The old `search_vector` TEXT column was unused. The new `search_tsvector` is auto-populated by Postgres. Existing data gets tsvectors on migration without a backfill.

### Rollback

Run `downgrade`. Drops the GIN indexes and generated columns, restores placeholder `search_vector`. FTS reverts to on-the-fly computation (slower but functional).

---

## P1b: Decision traces + resolution sessions

### Files to create/change


| File                                                                                         | Change                                           |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `backend/alembic/versions/0008_sessions_traces.py` (new)                                     | New tables                                       |
| `backend/src/contextedge/models/session.py` (new)                                            | SQLAlchemy models                                |
| `backend/src/contextedge/services/session_service.py` (new)                                  | CRUD logic                                       |
| `backend/src/contextedge/api/v1/sessions.py` (new)                                           | API router                                       |
| `[backend/src/contextedge/api/v1/__init__.py](backend/src/contextedge/api/v1/__init__.py)`   | Register sessions router                         |
| `[backend/src/contextedge/api/v1/runtime.py](backend/src/contextedge/api/v1/runtime.py)`     | Persist match breakdown to trace table           |
| `[backend/src/contextedge/schemas/playbook.py](backend/src/contextedge/schemas/playbook.py)` | Add `session_id` to RuntimeMatchRequest/Response |
| `[backend/src/contextedge/models/__init__.py](backend/src/contextedge/models/__init__.py)`   | Export new models                                |


### Schema migration (Alembic 0008)

```sql
CREATE TABLE resolution_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    external_case_ids JSONB DEFAULT '[]' NOT NULL,
    symptoms JSONB DEFAULT '[]' NOT NULL,
    entities JSONB DEFAULT '[]' NOT NULL,
    domain_id UUID REFERENCES domains(id),
    policy_snapshot JSONB DEFAULT '{}' NOT NULL,
    status VARCHAR(30) DEFAULT 'active' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    closed_at TIMESTAMPTZ,
    created_by UUID
);
CREATE INDEX ix_res_sessions_tenant_status ON resolution_sessions(tenant_id, status);

CREATE TABLE decision_trace_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES resolution_sessions(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    seq INT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    -- event_type values: retrieve, match, observe, branch, tool_call, human_input, playbook_step, feedback
    playbook_id UUID,
    playbook_version_id UUID,
    playbook_step_ref VARCHAR(100),
    inputs JSONB,
    outputs JSONB,
    branch_reason TEXT,
    confidence FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
CREATE INDEX ix_trace_events_session ON decision_trace_events(session_id);
CREATE INDEX ix_trace_events_tenant ON decision_trace_events(tenant_id);
```

### models/session.py

```python
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from contextedge.models.base import Base, TenantScopedMixin

class ResolutionSession(Base, TenantScopedMixin):
    __tablename__ = "resolution_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    external_case_ids: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    symptoms: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    entities: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True)
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    trace_events: Mapped[list["DecisionTraceEvent"]] = relationship(back_populates="session")

class DecisionTraceEvent(Base):
    __tablename__ = "decision_trace_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("resolution_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    playbook_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    playbook_step_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    inputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    outputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    branch_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    session: Mapped["ResolutionSession"] = relationship(back_populates="trace_events")
```

### API endpoints (sessions.py)

- `POST /sessions` -- create a resolution session (symptoms, entities, external_case_ids, domain_id).
- `GET /sessions/{session_id}` -- fetch session with trace events.
- `POST /sessions/{session_id}/events` -- append a decision trace event.
- `PATCH /sessions/{session_id}/close` -- close a session.

### runtime.py changes

Add optional `session_id: UUID | None` to `RuntimeMatchRequest`. When provided (or when creating a new session), after ranking completes, insert a `DecisionTraceEvent` with `event_type="retrieve"`, `inputs={"query_text": ..., "symptoms": ..., "entities": ...}`, `outputs={"results": [stable_keys...], "scores": [...]}`, `confidence=top_result.confidence`.

`RuntimeMatchResponse` gains optional `session_id: UUID | None`.

**Backward compatible:** `session_id` defaults to `None`. Existing callers omitting it get the same behavior (no session created, no trace written). Redis caching continues to work as before.

### Worker/job changes

None for P1b.

### Test cases to add

- `test_create_session`: create session via API, assert 201 with correct fields.
- `test_append_trace_event`: create session, append event, fetch session, assert event in trace.
- `test_runtime_match_with_session`: send `session_id` in match request, assert trace event persisted.
- `test_runtime_match_without_session`: send no `session_id`, assert no trace written (backward compat).

### Safe for current MVP?

Yes. New tables only. New optional API fields. Existing callers are unaffected.

### Rollback

Run `downgrade` migration to drop tables. Remove the new files. Revert runtime.py and schema changes. No data in other tables is affected.

---

## P1c: Object store wiring

### Files to create/change


| File                                                                                                                     | Change                                 |
| ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------- |
| `backend/src/contextedge/services/object_store.py` (new)                                                                 | MinIO async client                     |
| `[backend/src/contextedge/services/ingestion_persistence.py](backend/src/contextedge/services/ingestion_persistence.py)` | Offload large payloads to object store |
| `[backend/src/contextedge/main.py](backend/src/contextedge/main.py)`                                                     | Call `ensure_bucket()` on startup      |


### New module: services/object_store.py

```python
import io
from minio import Minio
from contextedge.config import settings

_client: Minio | None = None

def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=settings.minio_use_ssl,
        )
    return _client

def ensure_bucket():
    client = get_minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)

def upload_raw(tenant_id: str, raw_id: str, data: bytes) -> str:
    key = f"raw/{tenant_id}/{raw_id}.json"
    client = get_minio_client()
    client.put_object(settings.minio_bucket, key, io.BytesIO(data), len(data), content_type="application/json")
    return key

def download_raw(key: str) -> bytes:
    client = get_minio_client()
    response = client.get_object(settings.minio_bucket, key)
    return response.read()
```

### ingestion_persistence.py changes

After creating the `RawEvidenceObject`, check payload size:

```python
import json as json_mod
from contextedge.services.object_store import upload_raw

OFFLOAD_THRESHOLD_BYTES = 32_768

# Inside persist_ingestion_events, after line 78 (db.add(raw)):
payload_bytes = json_mod.dumps(payload, default=str).encode("utf-8")
if len(payload_bytes) > OFFLOAD_THRESHOLD_BYTES:
    raw.object_storage_key = upload_raw(str(tenant_id), str(raw.id), payload_bytes)
    raw.raw_payload = {"_offloaded": True, "size_bytes": len(payload_bytes)}
```

### main.py lifespan change

Add bucket creation in the `lifespan` function after Redis setup:

```python
from contextedge.services.object_store import ensure_bucket
try:
    ensure_bucket()
    logger.info("object_store_bucket_ready", bucket=settings.minio_bucket)
except Exception as exc:
    logger.warning("object_store_bucket_check_failed", error=str(exc))
```

### Schema migrations

None. The `object_storage_key` column already exists on `RawEvidenceObject`.

### API/interface changes

None visible externally.

### Worker/job changes

Workers that read `raw_payload` (in `extraction_tasks._normalize`) need to handle `{"_offloaded": True}`:

```python
# In _normalize, after fetching raw:
if raw.raw_payload and raw.raw_payload.get("_offloaded"):
    from contextedge.services.object_store import download_raw
    payload = json.loads(download_raw(raw.object_storage_key))
else:
    payload = raw.raw_payload or {}
```

### Test cases to add

- `test_small_payload_stays_in_postgres`: persist event with small payload, assert `object_storage_key` is None.
- `test_large_payload_offloads_to_minio`: mock `upload_raw`, persist event with >32KB payload, assert `object_storage_key` set and `raw_payload` has `_offloaded`.
- `test_normalize_reads_offloaded_payload`: mock `download_raw`, assert `_normalize` reconstitutes full payload.

### Safe for current MVP?

Yes. Existing small payloads continue as before. Only new large payloads get offloaded. Workers gracefully handle both.

### Rollback

Remove the offload logic. Existing offloaded rows will need a backfill script to restore `raw_payload` from MinIO (reversible since blobs are still in object store).

---

## P1d: Confidence separation

### Files to change


| File                                                                                                 | Change                                         |
| ---------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `[backend/src/contextedge/search/hybrid_ranker.py](backend/src/contextedge/search/hybrid_ranker.py)` | Separate confidence fields on `RankedPlaybook` |
| `[backend/src/contextedge/schemas/playbook.py](backend/src/contextedge/schemas/playbook.py)`         | Add new fields to `RuntimeMatchResult`         |
| `[backend/src/contextedge/api/v1/runtime.py](backend/src/contextedge/api/v1/runtime.py)`             | Populate new fields in response                |


### hybrid_ranker.py changes

`RankedPlaybook` dataclass adds:

```python
@dataclass
class RankedPlaybook:
    playbook: Playbook
    score: float                  # retrieval_score (weighted combo)
    confidence: float             # backward compat alias = score
    playbook_confidence: float    # from PlaybookVersion.playbook_confidence
    freshness_status: str
    evidence_count: int
    breakdown: dict = field(default_factory=dict)
```

In `rank_playbooks`, after computing `total`:

```python
pv = await db.get(PlaybookVersion, pv_id)
pb_confidence = pv.playbook_confidence if pv else 0.0

ranked.append(RankedPlaybook(
    playbook=pb,
    score=total,
    confidence=total,                    # deprecated, kept for compat
    playbook_confidence=pb_confidence,
    freshness_status=freshness_status,
    evidence_count=evidence_hits_pb,
    breakdown={...},
))
```

### Schema changes (playbook.py)

```python
class RuntimeMatchResult(BaseModel):
    playbook_id: UUID
    playbook_title: str
    stable_key: str
    match_score: float               # = retrieval_score
    confidence: float                # deprecated alias of match_score
    retrieval_score: float | None = None   # NEW: explicit retrieval signal
    playbook_confidence: float | None = None  # NEW: from playbook version
    freshness_status: str
    evidence_count: int
    risk_tier: str
    automation_mode: str
    scoring_breakdown: dict | None = None
```

### runtime.py changes

In the results loop (line 130-144), populate the new fields:

```python
RuntimeMatchResult(
    # ... existing fields ...
    retrieval_score=round(r.score, 4),
    playbook_confidence=round(r.playbook_confidence, 4),
)
```

### Schema migrations

None.

### API/interface changes

`POST /runtime/match` response `results[]` gains two optional fields: `retrieval_score`, `playbook_confidence`. Existing `confidence` and `match_score` continue to return the same value. **Fully backward compatible.**

### Worker/job changes

None.

### Test cases to add

- `test_match_response_includes_confidence_fields`: mock ranker, assert response has all three confidence fields.
- `test_playbook_confidence_from_version`: create playbook with version confidence 0.8, run match, assert `playbook_confidence=0.8`.

### Safe for current MVP?

Yes. Additive fields only.

### Rollback

Remove new fields. Existing callers see no change.

---

## P1e: Correlation worker + case_links

### Files to create/change


| File                                                                                                         | Change                                  |
| ------------------------------------------------------------------------------------------------------------ | --------------------------------------- |
| `backend/alembic/versions/0009_case_links.py` (new)                                                          | New table                               |
| `backend/src/contextedge/models/session.py`                                                                  | Add `CaseLink` model (or new file)      |
| `backend/src/contextedge/workers/correlation_tasks.py` (new)                                                 | Celery task                             |
| `[backend/src/contextedge/workers/celery_app.py](backend/src/contextedge/workers/celery_app.py)`             | Register task module, add queue routing |
| `[backend/src/contextedge/workers/extraction_tasks.py](backend/src/contextedge/workers/extraction_tasks.py)` | Chain correlation after normalize       |


### Schema migration (Alembic 0009)

```sql
CREATE TABLE case_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    canonical_case_id UUID NOT NULL,
    system VARCHAR(100) NOT NULL,         -- e.g., 'servicenow', 'slack', 'email'
    external_id VARCHAR(500) NOT NULL,
    evidence_id UUID REFERENCES evidence_items(id),
    confidence FLOAT DEFAULT 1.0 NOT NULL,
    first_seen TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    last_seen TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
CREATE INDEX ix_case_links_tenant_case ON case_links(tenant_id, canonical_case_id);
CREATE INDEX ix_case_links_external ON case_links(tenant_id, system, external_id);
```

### correlation_tasks.py

```python
@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def correlate_evidence(self, evidence_id: str, tenant_id: str):
    """Find cross-source correlations for a newly normalized evidence item."""
    async def work(db):
        # 1. Load the evidence item
        # 2. Extract external_id, thread_id, entity refs
        # 3. Search case_links for matching external_id or thread_id
        # 4. If match found, link to existing canonical_case_id
        # 5. If no match, create new canonical_case_id
        # 6. Write CorrelationEdge rows to link related evidence
        ...
    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
```

### extraction_tasks.py chain

After `normalize_evidence` dispatches `classify_relevance_task`, also dispatch `correlate_evidence`:

```python
# In normalize_evidence (line 160-161):
if res and "evidence_id" in res:
    classify_relevance_task.delay(res["evidence_id"], tenant_id)
    correlate_evidence.delay(res["evidence_id"], tenant_id)
```

### celery_app.py

Add to `task_routes`:

```python
"contextedge.workers.correlation_tasks.*": {"queue": "extraction"},
```

Add to `autodiscover_tasks`:

```python
"contextedge.workers.correlation_tasks",
```

### API/interface changes

New endpoint (optional for P1e, can defer to next iteration):

- `GET /cases/{canonical_case_id}/links` -- fetch all linked evidence across sources.

### Test cases to add

- `test_correlation_links_matching_external_id`: two evidence items with same external_id, assert `CaseLink` rows share `canonical_case_id`.
- `test_correlation_creates_new_case`: evidence with no matching external_id, assert new `canonical_case_id`.
- `test_normalize_chains_correlate`: mock both tasks, assert `correlate_evidence.delay` called after normalize.

### Safe for current MVP?

Yes. New table, new task. Existing normalize pipeline gains one additional `.delay()` call -- if correlation task fails, it retries independently and does not affect evidence normalization.

### Rollback

Drop `case_links` table. Remove `.delay()` call from `normalize_evidence`. Remove task file.

---

## P1f: Contradiction detection

### Files to create/change


| File                                                                                             | Change                        |
| ------------------------------------------------------------------------------------------------ | ----------------------------- |
| `backend/src/contextedge/services/contradiction_service.py` (new)                                | Detection logic               |
| `backend/src/contextedge/workers/evaluation_tasks.py`                                            | Add Celery beat task          |
| `[backend/src/contextedge/workers/celery_app.py](backend/src/contextedge/workers/celery_app.py)` | Add beat schedule             |
| `[backend/src/contextedge/graph/builder.py](backend/src/contextedge/graph/builder.py)`           | Add `contradicts` edge helper |


### contradiction_service.py

Core algorithm:

1. For each approved playbook in a domain, fetch its steps (from `PlaybookVersion.steps` JSON).
2. Fetch KB-type evidence items in the same domain (e.g., `evidence_type IN ('kb_article', 'sop', 'documentation')`).
3. For each KB article, compute semantic similarity against playbook step text.
4. If similarity is high but content contradicts (via LLM call), create/update a `Contradiction` row.
5. Add `graph_edges` row with `edge_type="contradicts"`.
6. Optionally call `send_notification(CONTRADICTION_ALERT, ...)`.

```python
async def scan_contradictions(db: AsyncSession, tenant_id: uuid.UUID, domain_id: uuid.UUID | None = None) -> dict:
    # Query approved playbooks in domain
    # For each, extract step texts
    # Query KB evidence in domain
    # Semantic + LLM comparison
    # Write Contradiction rows
    # Write graph edges
    # Return summary
    ...
```

### Celery beat addition

In `celery_app.py` `beat_schedule`:

```python
"scan-contradictions-every-12h": {
    "task": "contextedge.workers.evaluation_tasks.scan_contradictions_task",
    "schedule": 43200.0,
    "args": ("all",),
},
```

### Schema migrations

None. `Contradiction` model and `graph_edges` table already exist.

### API/interface changes

None new required (existing `/drift/alerts` could be extended to include contradiction alerts). Optional: `GET /contradictions?domain_id=...`.

### Worker/job changes

New Celery task `scan_contradictions_task` in `evaluation_tasks.py`.

### Test cases to add

- `test_contradiction_detected`: create playbook with step "restart service X", create KB evidence saying "never restart service X", mock LLM to confirm contradiction, assert `Contradiction` row created.
- `test_no_false_positive`: similar content that agrees, assert no contradiction created.

### Safe for current MVP?

Yes. New code only. No existing behavior modified. Beat schedule is additive.

### Rollback

Remove beat schedule entry. Remove task and service. Contradiction rows written during the window are inert data.

---

## P1g: Access-aware retrieval

### Files to change


| File                                                                                                 | Change                      |
| ---------------------------------------------------------------------------------------------------- | --------------------------- |
| `[backend/src/contextedge/search/vector_search.py](backend/src/contextedge/search/vector_search.py)` | Add access_policy filter    |
| `[backend/src/contextedge/search/hybrid_ranker.py](backend/src/contextedge/search/hybrid_ranker.py)` | Pass policy context through |
| `[backend/src/contextedge/search/pg_fts.py](backend/src/contextedge/search/pg_fts.py)`               | Add access_policy filter    |


### vector_search.py changes

Add optional `exclude_policy_ids` parameter:

```python
async def search_evidence_semantic(
    db, tenant_id, query_text, limit=20, *,
    query_embedding=None,
    exclude_policy_ids: list[uuid.UUID] | None = None,
):
    # ... existing setup ...
    stmt = select(EvidenceItem, distance).where(
        EvidenceItem.tenant_id == tenant_id,
        EvidenceItem.embedding.is_not(None),
    )
    if exclude_policy_ids:
        stmt = stmt.where(
            or_(
                EvidenceItem.access_policy_id.is_(None),
                EvidenceItem.access_policy_id.not_in(exclude_policy_ids),
            )
        )
    # ... rest unchanged
```

The policy resolution logic (which policy IDs to exclude based on the caller's role/domain) lives in the caller (`hybrid_ranker.py`).

### hybrid_ranker.py changes

Add `access_context` parameter to `rank_playbooks`. Before semantic search, resolve which `access_policy_id` values the caller cannot see:

```python
async def rank_playbooks(
    db, tenant_id, query_text, ...,
    caller_roles: list[str] | None = None,
):
    # Resolve excluded policies based on caller roles
    # For now: if not admin, exclude policies with config.restricted=True
    # Pass to vector search
    ...
```

### Schema migrations

None.

### API/interface changes

None externally. Retrieval results may differ based on caller permissions (which is the correct behavior).

### Worker/job changes

None.

### Test cases to add

- `test_restricted_evidence_excluded`: create evidence with restricted access policy, run search as non-admin, assert evidence excluded.
- `test_admin_sees_all_evidence`: run search as admin, assert restricted evidence included.

### Safe for current MVP?

Yes. Currently `access_policy_id` exists on evidence but is not enforced at retrieval time. This adds enforcement. Callers with no policy restrictions see identical results.

### Rollback

Remove the filter conditions. Retrieval reverts to unfiltered.

---

## Summary: dependency graph and deploy batches

```
Deploy 1 (P0) - code only, no migrations:
  P0a -> P0b -> P0c -> P0d -> P0e (all independent, can be one commit)

Deploy 2 (P1 batch 1) - backward-compatible migrations with one coordinated FTS DDL replacement:
  Migration 0007 (FTS indexes) -> P1a code
  Migration 0008 (sessions/traces) -> P1b code
  P1c (object store, no migration)
  P1d (confidence, no migration)

Deploy 3 (P1 batch 2) - backward-compatible migration:
  Migration 0009 (case_links) -> P1e code
  P1f (contradiction, no migration)
  P1g (access-aware, no migration)
```

The rollout stays API-backward-compatible, but the migrations are not all purely additive. `0007_fts_gin_indexes` drops placeholder FTS artifacts before adding generated stored `tsvector` columns and indexes, so deploy it in lockstep with the checked-in search code and avoid parallel old/new FTS code paths. `0008_resolution_sessions` and `0009_case_links` are additive. Rollback expectations should account for that coordinated `0007` replacement step rather than assuming every migration only drops newly added objects.

---

## Post-P1 gap remediation plan

### Gap-closure rollout order

```text
Deploy 4 (safety + event model)
  P2b  State clock vs event clock separation
  P2a  Governed execution + action safety classes

Deploy 5 (memory + knowledge quality)
  P2c  Identity resolution activation
  P2d  Explicit memory lifecycle separation
  P2e  Attachment/log extraction pipeline

Deploy 6 (platform hardening)
  P2f  Workspace/team boundary controls
  P2g  Ingestion control plane + storage lifecycle
```

The remaining gaps are no longer about basic MVP viability. They are about enterprise-safe execution, explicit memory semantics, stronger identity and provenance handling, and tighter operational controls.

## P2a: Governed execution + action safety classes

Implementation status: completed on `codex/p0-plan-implementation` via `0011_execution_governance`, `models/execution.py`, `schemas/execution.py`, `services/execution_service.py`, `api/v1/execution.py`, and `test_p2_execution.py` (9 tests). Safety-class enforcement, automatic approval-request creation for gated steps, tool-invocation ledger, operational-event emission, and session trace mirroring are all wired.

### Why it matters

The current system retrieves and traces approved playbooks, but it still does **not** provide enterprise-safe governed execution. The original ask required controlled agent execution, action safety classes, tool-call auditing, and approval gates for risky actions.

### Files to change/create


| File                                                          | Change                                                                    |
| ------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `backend/alembic/versions/0011_execution_governance.py` (new) | Add execution-run, step-run, tool-invocation, and approval-request tables |
| `backend/src/contextedge/models/execution.py` (new)           | ExecutionRun, ExecutionStepRun, ToolInvocation, ApprovalRequest models    |
| `backend/src/contextedge/schemas/execution.py` (new)          | Execution request/response schemas                                        |
| `backend/src/contextedge/services/execution_service.py` (new) | Runtime-to-execution orchestration, policy checks, approval gates         |
| `backend/src/contextedge/api/v1/execution.py` (new)           | Execution APIs                                                            |
| `backend/src/contextedge/api/v1/runtime.py`                   | Optional handoff from retrieval result to governed execution              |
| `backend/src/contextedge/models/playbook.py`                  | Persist step-level safety metadata                                        |
| `backend/src/contextedge/services/session_service.py`         | Mirror tool invocations into reasoning memory                             |


### Exact changes

- Add `ExecutionRun`, `ExecutionStepRun`, and `ToolInvocation` tables with tenant, session, playbook, actor, status, started/completed timestamps, and full input/output payloads.
- Add step-level fields such as `action_safety_class`, `requires_approval`, `tool_name`, and `expected_side_effect` to playbook version step payloads.
- Add `POST /execution/runs` and `POST /execution/runs/{run_id}/approvals/{approval_id}/decide` endpoints, plus list/detail/abort/complete execution APIs.
- Only allow execution from `approved` playbooks and disallow execution when `automation_mode` or caller role does not permit the requested safety class.
- Persist every tool invocation, branch decision, approval grant/deny, and abort into both decision traces and the event ledger from `P2b`.

Dependency note: `P2b` is now in place as the minimal operational-event ledger, so `P2a` can write execution events into a stable append-only store from the first governed-execution release.

### Type

Additive schema + new modules + runtime refactor.

### Expected impact

Closes the largest remaining product gap between “evidence-backed recommendation” and “safe operational execution.”

### Implementation risk

Medium. The main risk is over-coupling execution logic into `runtime.py`; keep execution orchestration in a separate service.

### Backward compatibility

Additive if execution is introduced as a new API path and existing retrieval endpoints remain unchanged.

## P2b: Explicit state clock vs event clock separation

Implementation status: completed on `codex/p0-plan-implementation` via `0010_operational_events`, `models/events.py`, `services/event_log_service.py`, request-scoped correlation/causation ids in `middleware/request_context.py`, and event emission from session, runtime, playbook, correlation, contradiction, and audit flows.

### Why it matters

The current code now has sessions, traces, and audit logs, but it still mixes “current truth” and “why it changed.” The original ask explicitly required a clean distinction between state clock and event clock.

### Files to change/create


| File                                                          | Change                                          |
| ------------------------------------------------------------- | ----------------------------------------------- |
| `backend/alembic/versions/0011_operational_events.py` (new)   | Add append-only operational event ledger        |
| `backend/src/contextedge/models/events.py` (new)              | OperationalEvent model                          |
| `backend/src/contextedge/services/event_log_service.py` (new) | Unified event append helpers                    |
| `backend/src/contextedge/services/playbook_service.py`        | Emit lifecycle events on transitions/versioning |
| `backend/src/contextedge/services/session_service.py`         | Emit reasoning and session events               |
| `backend/src/contextedge/services/correlation_service.py`     | Emit case-link/correlation events               |
| `backend/src/contextedge/services/contradiction_service.py`   | Emit contradiction events                       |
| `backend/src/contextedge/middleware/audit.py`                 | Reuse correlation/causation ids when present    |


### Exact changes

- Add `operational_events` with `tenant_id`, `entity_type`, `entity_id`, `session_id`, `event_type`, `occurred_at`, `recorded_at`, `causation_id`, `correlation_id`, `actor_id`, and JSON payload.
- Treat existing domain tables as current-state projections.
- Emit events for evidence ingestion, normalization, identity resolution, case correlation, contradiction detection, playbook transition, runtime retrieval, and execution steps.
- Add helper APIs or internal readers for replay/debug flows without forcing all reads through the event log.

### Type

Additive schema + cross-cutting refactor.

### Expected impact

Gives clear support for “what is true now” vs “why it happened,” and makes replay/debugging materially stronger.

### Implementation risk

Medium. The risk is event spam or inconsistent event emission. Start with the core state transitions only.

### Backward compatibility

Safe if events are append-only and existing read paths continue to use current-state tables.

## P2c: Identity resolution activation

Implementation status: completed on `codex/p0-plan-implementation` via `0012_evidence_identity_links`, active identity linking in `services/identity_service.py`, normalization wiring in `workers/extraction_tasks.py`, episode aggregation in `services/episode_service.py`, identity-aware correlation in `services/correlation_service.py`, graph edges for evidence/episodes/playbooks, and retrieval boosts in `search/hybrid_ranker.py`. Covered by `test_p2_identity_resolution.py` plus updated correlation/retrieval tests.

### Why it matters

The repo already has `CanonicalIdentity`, `IdentityAlias`, and `identity_service.py`, but they are not meaningfully wired into normalization, case correlation, or retrieval. This is still only partially implemented relative to the original ask.

### Files to change/create


| File                                                                                        | Change                                                   |
| ------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `backend/alembic/versions/0012_evidence_identity_links.py` (new)                            | Add evidence-to-identity link table                      |
| `backend/src/contextedge/models/episode.py` or `backend/src/contextedge/models/identity.py` | Add `EvidenceIdentityLink` if split out                  |
| `backend/src/contextedge/services/identity_service.py`                                      | Promote from helper to active pipeline component         |
| `backend/src/contextedge/workers/extraction_tasks.py`                                       | Resolve identities during normalization                  |
| `backend/src/contextedge/services/correlation_service.py`                                   | Use canonical identities in cross-source case linking    |
| `backend/src/contextedge/search/hybrid_ranker.py`                                           | Boost evidence/playbooks that share resolved identities  |
| `backend/src/contextedge/graph/builder.py`                                                  | Add edges from evidence/episodes/playbooks to identities |


### Exact changes

- During normalization, extract identities from title/body/raw payload and persist canonical/alias links.
- Add `EvidenceIdentityLink` with `identity_id`, `evidence_id`, `match_type`, `confidence`, and source metadata.
- Use identity overlap as a signal in case correlation and retrieval ranking.
- Record identity resolution output in reasoning memory and operational events.

### Type

Additive schema + worker refactor + ranking enhancement.

### Expected impact

Improves cross-system case correlation and reduces false grouping by raw external ids alone.

### Implementation risk

Medium. Bad identity merges are expensive. Start with high-confidence entity types such as users, hosts, tickets, and services.

### Backward compatibility

Additive. The current system can continue to operate when identity extraction fails or is disabled.

## P2d: Explicit memory lifecycle separation

Implementation status: completed on `codex/p0-plan-implementation` via `services/memory_service.py`, runtime assembly refactor in `api/v1/runtime.py`, long-term promotion hooks in `services/pattern_service.py` and `services/playbook_service.py`, explicit short-term/reasoning tagging in `services/session_service.py`, and memory-class-aware retention in `services/retention_service.py`. Covered by `test_p2_memory_service.py` plus updated runtime, playbook, and retention tests.

### Why it matters

Short-term, long-term, and reasoning memory now exist implicitly, but there is no explicit lifecycle or promotion model. The original ask wanted memory classes, not just separate tables.

### Files to change/create


| File                                                       | Change                                                              |
| ---------------------------------------------------------- | ------------------------------------------------------------------- |
| `backend/src/contextedge/services/memory_service.py` (new) | Central memory access/promotion/orchestration service               |
| `backend/src/contextedge/services/session_service.py`      | Treat resolution sessions as short-term memory backbone             |
| `backend/src/contextedge/services/pattern_service.py`      | Promote validated episode knowledge into long-term pattern memory   |
| `backend/src/contextedge/services/playbook_service.py`     | Promote approved playbooks into long-term memory tier               |
| `backend/src/contextedge/api/v1/runtime.py`                | Query memory service instead of assembling memory ad hoc            |
| `backend/src/contextedge/services/retention_service.py`    | Different retention for short-term vs long-term vs reasoning memory |


### Exact changes

- Boundary note: `operational_events` is the append-only system of record for event-clock history. `memory_service.py` in `P2d` should read projections plus selected ledger slices, not duplicate event emission through a second competing event path.
- Define explicit memory classes:
  - `short_term`: open sessions, active case context, recent evidence
  - `long_term`: canonical identities, validated patterns, approved playbooks
  - `reasoning`: decision traces, tool calls, approvals, failed/successful paths
- Add promotion rules from evidence -> episode -> pattern -> approved playbook.
- Add TTL/retention semantics per memory class and expose them in runbook/docs.
- Route runtime assembly through `memory_service.py` so memory behavior is explainable and testable.

### Type

Service-layer refactor with minimal schema change.

### Expected impact

Makes the product’s operational memory model explicit and easier to evolve without spreading retrieval rules across APIs.

### Implementation risk

Low to medium. Most risk comes from over-abstracting too early. Keep the first version thin.

### Backward compatibility

Safe if existing tables remain the underlying store and the new service is introduced as an orchestration layer.

## P2e: Attachment/log extraction pipeline

Implementation status: completed on `codex/p0-plan-implementation` for the deterministic `P2e.0` milestone via `0013_attachment_processing`, `services/artifact_extraction_service.py`, `workers/artifact_tasks.py`, `models/evidence.py`, `workers/extraction_tasks.py`, `api/v1/sources.py`, and `docs/RUNBOOK.md`. The current implementation supports text/log/json/transcript extraction, artifact object-store persistence, evidence-body merge with provenance markers, and follow-up classify/correlate after artifact processing. OCR-heavy document/image parsing remains intentionally deferred.

### Why it matters

The schema has `AttachmentArtifact`, object storage is wired, and connectors expose attachments in some cases, but attachment/log/transcript extraction is still weak relative to the original ask.

### Files to change/create


| File                                                                    | Change                                                   |
| ----------------------------------------------------------------------- | -------------------------------------------------------- |
| `backend/alembic/versions/0013_attachment_processing.py` (new)          | Optional artifact processing status/metadata fields      |
| `backend/src/contextedge/services/artifact_extraction_service.py` (new) | OCR/text/log parsing entry point                         |
| `backend/src/contextedge/workers/artifact_tasks.py` (new)               | Async extraction/parsing worker                          |
| `backend/src/contextedge/models/evidence.py`                            | Extend artifact metadata if needed                       |
| `backend/src/contextedge/api/v1/sources.py`                             | Capture uploaded artifacts and queue artifact extraction |
| `backend/src/contextedge/workers/extraction_tasks.py`                   | Link parsed artifact text back into normalized evidence  |
| `backend/src/contextedge/services/object_store.py`                      | Reuse for large binary/blob storage                      |


### Exact changes

- `P2e.0` shippable milestone: queue artifact processing, store metadata, and support deterministic `text/plain`, `.log`, `.json`, and transcript-style text inputs before OCR-heavy or document-heavy formats.
- Persist attachment metadata and binaries to object storage on ingest.
- Add async parsing for plain text logs, JSON logs, transcripts, and later OCR-capable document/image types.
- Store extracted text, parser type, parser confidence, and provenance on the artifact.
- Feed extracted text into episode reconstruction and contradiction detection with explicit provenance links.

### Type

New worker pipeline + optional additive schema.

### Expected impact

Closes a major support-operations gap because many high-value signals live in logs and attachments, not only chat/ticket bodies.

### Implementation risk

Medium. Parsing quality varies by artifact type. The deterministic first stage is low-risk; OCR-heavy formats should be added only after parser quality and cost controls are defined.

### Backward compatibility

Additive. Existing evidence ingestion can continue when artifact extraction is not yet enabled for a source.

## P2f: Workspace/team boundary controls

### Why it matters

Tenant boundaries and some domain/service-token controls exist, but workspace/team ABAC is still not enforced consistently across evidence, runtime, sessions, and playbook retrieval. The original ask explicitly called for multi-tenant and team-boundary controls.

### Files to change/create


| File                                               | Change                                                      |
| -------------------------------------------------- | ----------------------------------------------------------- |
| `backend/src/contextedge/deps.py`                  | Centralize effective scope resolution                       |
| `backend/src/contextedge/search/access_control.py` | Expand policy helper to include workspace/domain/team scope |
| `backend/src/contextedge/api/v1/evidence.py`       | Enforce workspace/domain visibility on reads                |
| `backend/src/contextedge/api/v1/runtime.py`        | Trim retrieval and playbook fetches by effective scope      |
| `backend/src/contextedge/api/v1/sessions.py`       | Prevent cross-team session access                           |
| `backend/src/contextedge/api/v1/playbooks.py`      | Enforce scope on playbook listing and retrieval             |
| `backend/tests/`                                   | Add workspace/domain ABAC coverage                          |


### Exact changes

- Compute an `effective_scope` object from tenant, workspace ids, allowed domain ids, service token claims, and role.
- Apply the same scope helper across evidence, runtime, sessions, playbooks, and audit queries.
- Record effective scope in audit logs and decision traces for later investigation.
- Keep platform/tenant admins as explicit override roles.
- Run a compatibility pass with seeded multi-workspace and multi-domain test data before enabling the tightened scope rules in production, because some existing broad queries will legitimately return fewer rows.

### Type

Cross-cutting refactor with test expansion.

### Expected impact

Moves access control from “mostly tenant + some domain” to something defensible for enterprise team separation.

### Implementation risk

Medium. Scope mistakes can either leak data or hide too much. Land this behind strong tests.

### Backward compatibility

Behavioral tightening only. Expect some existing broad queries to return fewer rows once scope is enforced.

## P2g: Ingestion control plane + storage lifecycle

### Why it matters

Sync runs, checkpoints, handoff recovery, and object-store offload exist, but there is still no operator-grade control plane for backlog visibility, dead-letter handling, or a clear hot/warm/cold storage strategy.

### Files to change/create


| File                                                      | Change                                                                      |
| --------------------------------------------------------- | --------------------------------------------------------------------------- |
| `backend/src/contextedge/api/v1/sync.py`                  | Expand operational visibility beyond raw run listing                        |
| `backend/src/contextedge/services/sync_worker_service.py` | Track backlog, retry, and dead-letter state explicitly                      |
| `backend/src/contextedge/services/retention_service.py`   | Add storage-tier transitions                                                |
| `backend/src/contextedge/models/evidence.py`              | Add storage tier / archive marker fields if needed                          |
| `backend/src/contextedge/api/v1/ingestion_admin.py` (new) | Operator APIs for replay/retry/quarantine                                   |
| `docs/RUNBOOK.md`                                         | Add operating procedures for backlog, replay, quarantine, and storage tiers |


### Exact changes

- Expose queue backlog, pending normalize ids, retry counts, and quarantine/dead-letter counts.
- Add replay/retry endpoints for failed ingestion and artifact extraction runs.
- Add explicit storage tiers:
  - `hot`: normalized rows + active raw payloads
  - `warm`: raw evidence in object storage only
  - `cold`: archived or export-only retention tier
- Extend retention logic to move data between tiers instead of only archiving state.
- Make replay idempotent by keying retries to raw object ids / run ids and add per-tenant quotas early so the control plane cannot be abused to exhaust storage or worker capacity.

### Type

Service refactor + optional additive schema + ops/API additions.

### Expected impact

Makes ingestion and retention operationally manageable instead of relying on ad hoc debugging.

### Implementation risk

Low to medium. Most complexity is in operator ergonomics and failure-mode handling, not core business logic.

### Backward compatibility

Safe if new control-plane APIs are additive and storage-tier transitions are introduced gradually.

## Recommended sequence for the next implementation turn

1. `P2f` Workspace/team boundary controls
2. `P2g` Ingestion control plane + storage lifecycle

`P2a`, `P2b`, `P2c`, `P2d`, and the deterministic `P2e.0` artifact-extraction milestone are now complete. The remaining work is boundary control and ingestion operations, which should be landed before any broader OCR/document-extraction expansion.
