---
name: Multi-Tenant Consistency Fix
overview: Fix multi-tenant isolation inconsistencies across background tasks, services, models, and frontend to ensure complete tenant data isolation and consistent patterns throughout the codebase.
todos:
  - id: fix-autonomous-tasks
    content: Fix autonomous_tasks.py to iterate per tenant
    status: completed
  - id: fix-enrichment-tasks
    content: Fix enrichment_tasks.py to iterate per tenant
    status: completed
  - id: fix-ai-enrichment
    content: Add tenant_id validation to ai_enrichment.py functions
    status: completed
  - id: fix-war-room-service
    content: Add tenant_id to war_room_service.py get methods
    status: completed
  - id: fix-telemetry-service
    content: Add tenant filter to telemetry_service.py CI query
    status: completed
  - id: add-tenant-id-models
    content: Add tenant_id to ChatMessage and WarRoomMessage models
    status: completed
  - id: create-migration
    content: Create Alembic migration for new tenant_id columns
    status: completed
  - id: fix-frontend-tenant
    content: Fix hardcoded tenant ID in TenantManagement.tsx
    status: completed
  - id: cleanup-tenancy-utils
    content: Remove unused apply_tenant_filter from tenancy.py
    status: completed
---

# Multi-Tenant Consistency Fix

## Problem Summary

The codebase has several multi-tenant isolation gaps that could lead to cross-tenant data leakage:
1. Background tasks process data across all tenants without filtering
2. Services fetch by ID without tenant ownership validation
3. Some models lack direct `tenant_id` for efficient filtering
4. Frontend has hardcoded tenant ID and inconsistent patterns

---

## Phase 1: Fix Background Tasks (Critical)

### 1.1 Fix `autonomous_tasks.py`
**File:** [`backend/app/tasks/autonomous_tasks.py`](backend/app/tasks/autonomous_tasks.py)

Current issue (line 19-21):
```python
incidents = db.query(Incident).filter(
    Incident.status == IncidentStatus.NEW
).limit(20).all()  # No tenant filter!
```

Fix: Iterate per tenant like other tasks do:
```python
tenants = db.query(Tenant).filter(Tenant.is_active == True).all()
for tenant in tenants:
    incidents = db.query(Incident).filter(
        Incident.tenant_id == tenant.id,
        Incident.status == IncidentStatus.NEW
    ).limit(20).all()
```

### 1.2 Fix `enrichment_tasks.py`
**File:** [`backend/app/tasks/enrichment_tasks.py`](backend/app/tasks/enrichment_tasks.py)

Fix both `enrich_new_incidents()` and `correlate_new_alerts()` to iterate per tenant.

---

## Phase 2: Fix Service Layer Tenant Validation

### 2.1 Fix `ai_enrichment.py`
**File:** [`backend/app/services/ai_enrichment.py`](backend/app/services/ai_enrichment.py)

Add tenant validation to functions that query by ID:
- `enrich_incident_advanced()` - Add tenant_id parameter and validation
- `enrich_incident()` - Add tenant_id parameter and validation
- `correlate_alert_to_ci()` - Add tenant_id parameter and validation
- `_get_ci_suggestions()` - Already filtered, but verify

### 2.2 Fix `war_room_service.py`
**File:** [`backend/app/services/war_room_service.py`](backend/app/services/war_room_service.py)

Update `get_war_room()` (line 617-619) to require tenant_id:
```python
def get_war_room(self, war_room_id: int, tenant_id: int) -> Optional[WarRoom]:
    return self.db.query(WarRoom).filter(
        WarRoom.id == war_room_id,
        WarRoom.tenant_id == tenant_id
    ).first()
```

### 2.3 Fix `telemetry_service.py`
**File:** [`backend/app/services/telemetry_service.py`](backend/app/services/telemetry_service.py)

Add tenant validation when querying CI (line 211-213):
```python
ci = self.db.query(ConfigurationItem).filter(
    ConfigurationItem.id == incident.affected_ci_id,
    ConfigurationItem.tenant_id == incident.tenant_id  # Add this
).first()
```

---

## Phase 3: Model Updates for Direct Tenant Filtering

### 3.1 Add `tenant_id` to `ChatMessage`
**File:** [`backend/app/models/chat_conversation.py`](backend/app/models/chat_conversation.py)

Add denormalized `tenant_id` for direct filtering:
```python
tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
```

### 3.2 Add `tenant_id` to `WarRoomMessage`
**File:** [`backend/app/models/war_room.py`](backend/app/models/war_room.py)

Add denormalized `tenant_id` for direct filtering:
```python
tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
```

### 3.3 Create Database Migration
Create Alembic migration to add the new columns and backfill from parent tables.

---

## Phase 4: Frontend Fixes

### 4.1 Fix hardcoded tenant ID in `TenantManagement.tsx`
**File:** [`frontend/src/pages/TenantManagement.tsx`](frontend/src/pages/TenantManagement.tsx)

Remove hardcoded `tenantId = 1` (line 122) and use auth context:
```typescript
const { user } = useAuth();
const tenantId = user?.tenant_id;
```

### 4.2 Ensure consistent tenant handling
Verify all frontend components use tenant from auth context, not hardcoded values.

---

## Phase 5: Standardize Tenant Filtering Pattern

### 5.1 Remove or implement `apply_tenant_filter()`
**File:** [`backend/app/core/tenancy.py`](backend/app/core/tenancy.py)

Option A: Remove unused function to reduce confusion
Option B: Implement consistently across services (more work, less benefit)

Recommendation: Remove it and document the standard pattern (`current_user.tenant_id` for API, explicit parameter for services).

### 5.2 Standardize webhook tenant identification
**Files:**
- [`backend/app/api/v1/endpoints/webhooks.py`](backend/app/api/v1/endpoints/webhooks.py) - Uses `tenant_slug` (good)
- [`backend/app/api/v1/endpoints/integrations.py`](backend/app/api/v1/endpoints/integrations.py) - Uses `tenant_id` (integer)

Consider switching Teams/Slack webhooks to use `tenant_slug` for consistency and security.

---

## Files to Modify

| File | Changes |
|------|---------|
| `backend/app/tasks/autonomous_tasks.py` | Add per-tenant iteration |
| `backend/app/tasks/enrichment_tasks.py` | Add per-tenant iteration |
| `backend/app/services/ai_enrichment.py` | Add tenant_id validation |
| `backend/app/services/war_room_service.py` | Add tenant_id to get methods |
| `backend/app/services/telemetry_service.py` | Add tenant filter to CI query |
| `backend/app/models/chat_conversation.py` | Add tenant_id to ChatMessage |
| `backend/app/models/war_room.py` | Add tenant_id to WarRoomMessage |
| `backend/app/core/tenancy.py` | Remove unused apply_tenant_filter |
| `frontend/src/pages/TenantManagement.tsx` | Fix hardcoded tenant ID |
| `backend/alembic/versions/` | New migration for model changes |

---

## Testing Approach

1. Verify background tasks only process their tenant's data
2. Verify services return 404 for cross-tenant ID access
3. Run existing test suite to ensure no regressions
4. Manual verification of frontend tenant handling