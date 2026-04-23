# Human-in-the-Loop (HITL)

One-line summary: a `Human Approval` node can pause a workflow and resume it with an audited decision from an operator. Every resume — approve, reject, timeout-rejected, timeout-escalated — writes one row to `approval_audit_log`, which is the single source of truth for the compliance story.

## 1. Lifecycle

```
 running ──┐
           ▼
       suspended
         status=suspended
         current_node_id=<hitl-node>
           │
           ├──► POST /callback { decision="approved", approver, reason?, context_patch? }
           │       └──► append approval_audit_log row
           │       └──► enqueue resume_workflow_task → running → …
           │
           ├──► POST /callback { decision="rejected", approver, reason }
           │       └──► append approval_audit_log row
           │       └──► status=failed, suspended_reason="rejected"
           │            (maps to A2A v1.0 "rejected" state via A2A-01.b)
           │
           ├──► HITL-01.c scheduler sweep (timeout elapsed)
           │       └──► append approval_audit_log row with
           │            decision="timeout_rejected" | "timeout_escalated"
           │
           └──► parent workflow cancelled
                   └──► cancel cascades to child instance
```

## 2. Ticket timeline

| Ticket | Status | What it adds |
|---|---|---|
| **HITL-01.a** | **Shipped** | Audit table + claimed-identity capture on resume + `GET /approvals` list + redesigned dialog with approver/reason/advanced-patch |
| **HITL-01.b** | **Shipped** | Pending-approvals toolbar badge (pulsing amber dot when count > 0) + aggregated dropdown grouped by workflow + 30s poll; `GET /pending-approvals` backend endpoint; `suspended_at` column (migration 0031) |
| **HITL-01.c** | Planned | Timeout enforcement via Beat sweep: `timeoutAction: "reject" \| "escalate" \| "none"` |
| **HITL-01.d** | Planned | Per-node approvers allowlist (`approvers: {emails, allowlist}`); 403 on non-allowlisted attempts; audit still captures the attempt |
| **HITL-01.e** | Planned | Notification channels on suspend (Slack / email / webhook) |
| **HITL-01.f** | Planned | Bubble child sub-workflow HITL up to the parent so composable workflows can use approvals |

## 3. The audit log

Migration 0030 creates `approval_audit_log`:

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `tenant_id` | RLS-scoped |
| `instance_id` | FK → `workflow_instances.id` ON DELETE CASCADE |
| `node_id` | The HITL node the instance was paused at |
| `parent_instance_id` | NULL today; reserved for HITL-01.f bubble-up |
| `approver` | **Claimed** identity the caller sent. Protected today only by tenant-scoped bearer auth — treat as attested, not cryptographically verified, until OIDC integration lands |
| `decision` | One of `"approved"`, `"rejected"`, `"timeout_rejected"`, `"timeout_escalated"` |
| `reason` | Free-form note (≤2000 chars). Required when rejecting (enforced by the dialog) |
| `context_before_json` | Instance `context_json` snapshotted immediately before the operator's patch merges — "what did the approver see?" |
| `context_after_json` | Post-merge snapshot — includes `approval_payload` under `"approval"` + any keys from `context_patch` |
| `approvers_allowlist_matched` | NULL today; HITL-01.d flips to `true` / `false` once allowlist enforcement ships |
| `created_at` | Timestamp with tz |

Indexes: `(tenant_id, created_at)` for the compliance dashboard, `(instance_id, created_at)` for per-instance drill-in. Tenant-scoped RLS keyed on `tenant_id`.

## 4. Identity honesty

We don't have OIDC today. The resume endpoint reads `approver` from the request body, protected by the tenant-scoped bearer header. Any member of the tenant can claim to be anyone inside that tenant; the audit row records the claim.

HITL-01.d adds a per-node allowlist so the endpoint can 403 a non-approved approver. HITL-01.d's 403 path still writes an audit row (with `approvers_allowlist_matched=false`) so bypass attempts are visible.

A future `verified_by_oidc` column will upgrade the audit from attested to verified once real IAM lands.

## 5. API surface

### Resume / approve / reject

```http
POST /api/v1/workflows/{workflow_id}/instances/{instance_id}/callback
Authorization: Bearer <tenant-key>
Content-Type: application/json

{
  "approver": "alice@acme.example",
  "decision": "approved",              // or "rejected"
  "reason": "Double-checked with ops.",
  "approval_payload": {"ok": true},
  "context_patch": {"node_3": {"score": 0.95}}
}
```

Back-compat: `approver`, `decision`, and `reason` are all optional. Omitted `decision` defaults to `"approved"` (the v0 path); omitted `approver` defaults to `"anonymous"` in the audit row. The frontend dialog always sends all three.

### Pending approvals (HITL-01.b)

```http
GET /api/v1/workflows/pending-approvals
Authorization: Bearer <tenant-key>
```

Returns the tenant's backlog of **human** suspensions (filters out `async_external`) ordered oldest-first. Response shape per row:

```json
{
  "instance_id": "...",
  "workflow_id": "...",
  "workflow_name": "Slack summariser",
  "node_id": "node_4",
  "approval_message": "Confirm the recipient list is correct.",
  "suspended_at": "2026-04-23T10:00:00Z",
  "age_seconds": 7200
}
```

`age_seconds` is computed from `suspended_at` (falls back to `started_at` for v0 rows that suspended before migration 0031 stamped the column). The toolbar dropdown renders it as `3m ago` / `2h ago` / `3d ago`; the absolute timestamp shows on hover.

### Compliance export

```http
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/approvals
```

Returns the audit rows for this instance ordered oldest-first. Useful for answering "walk me through what happened" audits and for the HITL-01.b dashboard's drill-in (future slice).

## 6. UX — HITLResumeDialog

The dialog (`frontend/src/components/toolbar/HITLResumeDialog.tsx`) was redesigned in HITL-01.a:

1. **Approval message banner** — amber AlertTriangle callout at the top surfacing the node's `approvalMessage` config string.
2. **"Approving as"** — claimed-identity input, pre-filled from `localStorage["aeai.hitl.approver"]`. First-time users see a blue "stored locally — single sign-on lands in a later slice" info banner; returning users see a clean input with no hint.
3. **Reason textarea** — required when rejecting (dialog-side enforcement so the operator gets the error before hitting the API), optional when approving. Max 2000 chars.
4. **Advanced accordion** — collapsed by default. Contains the context-snapshot read-only viewer + the JSON context-patch editor. Most approvals never expand it.
5. **Footer** — `Reject` (destructive outline) + `Approve & resume` (primary). Enter fires approve.

The approver name persists to localStorage on every successful submit, so repeat approvers see it pre-filled next time.

## 7. Toolbar pending-approvals button (HITL-01.b)

Discovery UX — operators see at a glance how many approvals are waiting without opening the Execution Panel per instance.

- **Icon**: Bell (universal notifications).
- **Quiet state** (count = 0): just the bell, no badge, no pulse — doesn't fight for attention.
- **Active state** (count > 0): pulsing amber dot over the bell + count badge at the bottom-right (capped at `9+`). Amber matches the suspended-state language used everywhere else in the UI.
- **Dropdown**: grouped by workflow name. Each row shows the approval message (line-clamped at 2) + `node_id` + relative age.
- **Click-through**: selecting a row opens the existing `HITLResumeDialog` pre-filled with the instance's full context.
- **Refresh**: polls every 30s while mounted; explicit refresh button in the dropdown header; auto-refreshes when the dialog closes so the count drops the moment an approval lands.
- **Empty state**: centred sentence "No approvals waiting — nice." with a muted explainer — human, not sterile.

## 8. Tests

- `backend/tests/test_hitl_audit_log.py` — 8 tests: approve writes row + queues resume; reject writes row + closes instance + skips Celery; v0 back-compat (no approver defaults to anonymous); invalid decision → 422; over-long approver → 422; non-suspended instance → 404; list returns ordered rows; list 404s on missing instance.
- `backend/tests/test_hitl_pending_approvals.py` (HITL-01.b) — 5 tests: rows returned oldest-first with correct `age_seconds`, empty list when nothing pending, `started_at` fallback for pre-migration-0031 rows, malformed rows with no timestamps skipped, missing node in graph still returns the row with `approval_message: null`.
- `frontend/src/components/toolbar/HITLResumeDialog.test.tsx` — 6 tests: first-time hint shows, returning approver pre-fills from localStorage, empty approver blocked with inline error, rejecting without a reason blocked, approve forwards `{approver, decision, reason}` to the store + persists localStorage, reject mirror.
- `frontend/src/components/toolbar/PendingApprovalsButton.test.tsx` (HITL-01.b) — 11 tests: bell quiet with zero pending, pulsing amber dot + count badge when > 0, badge caps at `9+`, dropdown empty state renders the friendly copy, dropdown groups rows by workflow name, plus 6 `formatAge` parametrised cases (just-now / Nm ago / Nh ago / Nd ago).
