# AutomationEdge Node

The `AutomationEdge` node submits a workflow to an AutomationEdge (AE) server — the orchestrator's first integration with an async-external RPA system. While AE runs the automation (which can take seconds to hours), the parent workflow is **suspended**: no worker thread is held, the DB row carries `suspended_reason = 'async_external'`, and resumption is driven by either the Beat poller (default) or an opt-in webhook callback. Downstream nodes receive the AE response payload merged into the workflow context.

**Tested against AE REST API v5.4+**. Earlier releases should work as long as the three endpoints documented in §3 exist with the same shape.

---

## 1. Overview

```
Trigger ─▶ LLM Agent ─▶ AutomationEdge ──►(suspend)──►  AE server
                                                         │
                                                         │  Beat polls every ~30s
                                                         │  OR webhook callback
                                                         ▼
                       ┌──── resume ───── terminal status received
                       ▼
                  Condition ─▶ ...
```

- **Pattern C (default)** — Beat task `poll_async_jobs` re-queries AE's `workflowinstances/{id}` endpoint on the configured interval. When AE reports a terminal status (`Complete` / `Failure` / `Failed` / `Terminated`), the parent instance resumes with the full AE response in `node_X.ae_response`.
- **Pattern A (opt-in)** — the AE workflow author adds a terminal HTTP step that posts back to `/api/v1/async-jobs/{id}/complete`. Immediate resumption, no Beat latency.

Both modes resume through the exact same code path (`finalize_terminal` in `app/engine/async_job_finalizer.py`), so the context shape downstream nodes see is identical.

---

## 2. One-time operator setup

Two pieces of tenant-scoped config need to exist before an `AutomationEdge` node can run end-to-end.

### 2a. Add AE credentials to the Secrets vault

Click the 🗝️ **Secrets** icon in the toolbar and add the two keys the node needs. The exact names depend on the `credentialsSecretPrefix` you set on the integration (default `AUTOMATIONEDGE`) and on the `authMode`:

| authMode | Required secrets |
|---|---|
| `ae_session` (default) | `<prefix>_USERNAME`, `<prefix>_PASSWORD` |
| `bearer` | `<prefix>_TOKEN` |

Example (prefix = `AUTOMATIONEDGE`, ae_session mode):

```
AUTOMATIONEDGE_USERNAME     =  orch_api_user@aedemo
AUTOMATIONEDGE_PASSWORD     =  <the AE password>
```

Secrets are Fernet-encrypted at rest and never returned through any API (§5.2a of `SETUP_GUIDE.md`). The orchestrator reads them per-request via `get_tenant_secret(tenant_id, key_name)`.

### 2b. Add a tenant integration

Click the 🤖 **Integrations** icon in the toolbar and **Add Integration** for system `automationedge`. The dialog stores the connection defaults so individual `AutomationEdge` nodes can leave most of their config blank.

| Field | Example | Notes |
|---|---|---|
| Label | `prod-ae` | Referenced from nodes as `integrationLabel`. Unique per tenant. |
| AE REST base URL | `http://ae.example.com:8080/aeengine/rest` | Ends with `/rest`. Matches the `<base>/rest/authenticate` path AE docs describe. |
| AE orgCode | `AEDEMO` | AE's tenant identifier — distinct from our orchestrator tenant_id. |
| Auth mode | `ae_session` or `bearer` | §2a. |
| Credentials secret prefix | `AUTOMATIONEDGE` | Must match the Secret key prefix used in §2a. |
| Source tag | `AE AI Hub Orchestrator` | Recorded on the AE side as the request source. |
| AE userId | `orchestrator` | Recorded on the AE request row for audit. |
| **Use as default** | ☑ | Recommended — nodes with a blank `integrationLabel` will pick this row. |

You can have multiple integrations (e.g. `dev-ae` and `prod-ae`) and switch between them per-node by setting `integrationLabel` on the node config. Exactly one integration per (tenant, system) can be the default.

---

## 3. What the node does on the wire

```
POST  <base>/authenticate           (once per process, token cached)
POST  <base>/execute                (submit — returns automationRequestId)
GET   <base>/workflowinstances/{id} (Beat polls this until terminal)
PUT   <base>/workflowinstances/{id}/terminate  (best-effort on cancel)
```

### Submit body

The `/execute` POST body is constructed from the node config plus the resolved integration. The wire shape matches the AE 5.4 REST guide § 5.1:

```json
{
  "orgCode": "AEDEMO",
  "workflowName": "Reset VPN For User",
  "userId": "orchestrator",
  "source": "AE AI Hub Orchestrator",
  "sourceId": "<our workflow_instance UUID>",
  "responseMailSubject": null,
  "params": [
    {"name": "user_email", "value": "alice@corp", "type": "String"},
    {"name": "ticket_id",  "value": "INC-12345",  "type": "String"}
  ],
  "inputAttribute1": null, "inputAttribute2": null, ..., "inputAttribute6": null
}
```

Response fields we care about:

- `success` — rejection on `false` (`RuntimeError`).
- `automationRequestId` — the integer stored as `async_jobs.external_job_id`.

### Status response

AE's `GET /workflowinstances/{id}` returns a rich object. We interpret `status` as follows:

| AE `status` | Orchestrator action |
|---|---|
| `Complete`, `Success` | Terminal → parent resumes, node output state = `completed` |
| `Failure`, `Failed`  | Terminal → parent resumes, node output state = `failed`, `error` field populated |
| `Terminated` | Terminal → parent resumes, node output state = `cancelled` |
| `New`, `Executing` | In-flight — keep polling |
| `Diverted` | **Held for human intervention in AE** — keep polling, but the active-runtime timeout clock **pauses** while the job is in this state. See §5. |
| anything else | Treated as in-flight (keep polling). The `Pending` hint appears in some AE builds. |

The full response body is preserved on `node_X.ae_response`, and `workflowResponse` (which AE emits as a JSON-**encoded string**) is parsed into `node_X.ae_response.workflow_response_parsed` for convenient access. AE `outputParameters` are promoted to `node_X.output_parameters` for quick branching.

---

## 4. Using the node on the canvas

Drag the 🤖 AutomationEdge node in. Typical config for a production workflow:

```yaml
# Node config (leave integration fields blank to use the tenant default)
integrationLabel: ""                   # or "dev-ae" for a specific override
workflowName: "Reset VPN For User"
inputMapping:
  - { name: user_email, valueExpression: "trigger.message.email", type: "String" }
  - { name: ticket_id,  valueExpression: "node_3.intent_entities.ticket",  type: "String" }
completionMode: "poll"                 # or "webhook" — see §6
pollIntervalSeconds: 30                # Beat re-polls AE at this cadence for this job
timeoutSeconds: 3600                   # 1h active-runtime cap (Diverted time excluded)
maxDivertedSeconds: 604800             # 7d cap on a single Diverted span (safety net)
```

`inputMapping` expressions go through the orchestrator's `safe_eval`, so they can reference any upstream node output (`node_2.response`, `node_2.output_parameters.foo`) or the trigger payload (`trigger.session_id`, etc.).

### Branching on the AE outcome

Downstream Condition nodes can route on the node output. The shape is:

```json
{
  "state": "completed",                 // "completed" | "failed" | "cancelled" | "timed_out"
  "external_job_id": "2968",
  "output_parameters": {"result": "ok", "vpn_profile_reset": true},
  "ae_message": "Execution Successful",
  "ae_response": { ...full AE /workflowinstances/{id} body... },
  // On failure:
  "failureReason": "SAPServiceUnavailable",
  "failureReasonDescription": "SAP returned 503",
  "error": "SAPServiceUnavailable",
  // On timeout only:
  "_warning": true
}
```

Typical Condition expressions:

```
node_4.state == "completed"
node_4.state == "completed" and node_4.output_parameters.vpn_profile_reset == true
node_4.state in ["failed", "timed_out"]
```

---

## 5. Diverted handling — pause-the-clock

AE's `Diverted` state means the process is paused waiting for a human to act **in AE's own UI** (assign agent, approve, supply input, etc.). Two independent budgets govern how long we'll wait:

- **`timeoutSeconds`** — default 3600 (1 h). Applies to **active runtime only**. Time the job spent in `Diverted` is excluded from this budget.
- **`maxDivertedSeconds`** — default 604800 (7 d). Applies to the **current** Diverted span. A failsafe against abandoned diverts.

Example: a job sits Diverted for 2 days waiting on a compliance officer, then un-diverts and runs for 40 more minutes. `timeoutSeconds` counts 40 minutes, `maxDivertedSeconds` counts 2 days. Neither trips.

Both budgets can be tuned per-node. The UI's cyan waiting badge shows the current Diverted span plus the total of all prior Diverted spans:

> `Waiting on AutomationEdge · 2h 4m`
> `· Diverted in AutomationEdge · awaiting operator · 45s (total 3m 12s)`

On timeout, the parent resumes with `node_X.state = "timed_out"` and `node_X._warning = true` — downstream Condition nodes can branch on either.

---

## 6. Webhook mode (Pattern A opt-in)

Webhook mode eliminates the 30s Beat-poll latency by having the AE workflow call back into the orchestrator directly on completion. AE doesn't have a built-in "notify orchestrator on done" feature, so the AE workflow author must add a terminal **HTTP Request** step. The orchestrator hands the callback URL and auth secret to the AE workflow as input parameters at submit time.

### 6a. Node config

```yaml
completionMode: "webhook"
webhookAuth: "token"                     # "token" | "hmac" | "both"
webhookCallbackBaseUrl: "https://orch.example.com"   # orchestrator public URL
webhookCallbackParamName: "callback_url"             # AE workflow param receiving the URL
webhookTokenParamName:    "callback_token"           # AE workflow param receiving the token (token/both modes)
```

At submit time the orchestrator generates a per-job secret and passes it to AE as one of the `params`:

```json
{"name": "callback_url",   "value": "https://orch.example.com/api/v1/async-jobs/<uuid>/complete", "type": "String"},
{"name": "callback_token", "value": "<per-job URL-safe secret>", "type": "String"}
```

### 6b. Designing the AE workflow

The AE workflow must declare the two input params above (or however many of them, depending on `webhookAuth`), and add an HTTP Request step at the end that:

| Auth mode | HTTP request shape |
|---|---|
| `token` | `POST ${callback_url}?token=${callback_token}` with body = `{"status": "Complete", "workflowResponse": ...}` |
| `hmac` | `POST ${callback_url}` with header `X-AE-Signature: sha256=<hex(HMAC-SHA256(secret, body))>` |
| `both` | Either of the above. Endpoint accepts either mechanism. |

On failure branches in the AE workflow, post the same shape with `status: "Failure"` and a `failureReason` field.

### 6c. Security posture

- **Tokens are per-job** — generated at submit, destroyed when the job is finalised. A stolen token unlocks at most one completion callback.
- **`hmac` mode** covers the body — an attacker who intercepts the URL can't replay with a modified payload.
- The endpoint returns **401 for every failure** (unknown job, wrong token, bad signature) so a misconfigured callback can't be used as an oracle to probe which job UUIDs exist.
- Re-delivery of a completed callback is **idempotent** — the endpoint returns `{"ok": true, "note": "already finalised"}` without re-dispatching the parent resume.
- Non-terminal status (`Executing`, `Diverted`) arriving via webhook is **ignored** (early-callback bug protection). Beat keeps polling.

### 6d. When to pick which mode

- **Default Pattern C (poll)** — no firewall work, works in air-gapped setups, adds ~30s median latency.
- **Pattern A (webhook)** — sub-second resumption once AE fires, requires inbound HTTP path to the orchestrator and operator effort in AE Studio to wire the HTTP step. Best for high-frequency or latency-sensitive automations.

Many operators start on Pattern C and flip individual high-volume nodes to Pattern A later.

---

## 7. Cancellation

When an operator cancels the parent workflow (via the UI **Stop** button or `POST .../cancel`), the orchestrator:

1. Flips the parent instance to `status=cancelled`.
2. For every outstanding `async_jobs` row on that instance: marks it `cancelled` in our DB and best-effort calls `PUT <base>/workflowinstances/{id}/terminate` on AE.

**AE 5.4 does not document a terminate endpoint.** The orchestrator swallows a 404 and moves on — our side stops polling, but the AE process **continues to run** until it completes naturally. The cancel only covers our side of the ledger.

If your AE deployment has a documented terminate (newer AE builds may expose one), point `AEConnection.try_terminate` at the correct path in `app/engine/automationedge_client.py` and it will be picked up.

---

## 8. Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Node errors immediately with `Missing vault secrets <PREFIX>_USERNAME/<PREFIX>_PASSWORD` | Credentials not in the Secrets vault, or prefix mismatch between the integration and the secrets | §2a. Verify the prefix on the integration matches the secret key names. |
| `AE /execute returned 400` | `orgCode` not set on the integration, or `workflowName` doesn't exist on the AE server under that org | Log body is redacted in execution_logs; re-check the integration config and AE Studio. |
| Job stays in `submitted` forever in the UI | Beat worker not running, OR the `ae_orchestrator_beat` BYPASSRLS role isn't configured (§5.2a of `SETUP_GUIDE.md`) | `celery -A app.workers.celery_app beat` logs + `SELECT * FROM async_jobs WHERE status='submitted'` |
| Webhook callback hits `401 invalid webhook credentials` | Token / HMAC mismatch, or the AE workflow is firing against the wrong URL | Check the AE workflow's HTTP step config. Orchestrator never logs *which* check failed — that's deliberate. |
| Resume fires but downstream node can't find its data | AE emitted `workflowResponse` that isn't a JSON-encoded string | Check `node_X.ae_response.workflowResponse` (raw); `workflow_response_parsed` and `output_parameters` will be absent. |
| Cyan "waiting" badge still shows after AE finished | Beat hasn't polled yet (up to `pollIntervalSeconds`) or `fetchInstanceAsyncJobs` failed transiently | Wait one cadence; check browser devtools for 4xx on the async-jobs endpoint. |

Every async job keeps its `last_error` field around — non-fatal polling errors (network blips, AE 5xx) accumulate there without failing the node. Operators can inspect the full history via direct DB query once S1-12's integration APIs land.

---

## 9. Code map

Everything this guide describes lives in a small set of modules, useful when extending or debugging:

| Concern | File |
|---|---|
| REST client (login, execute, get_status, try_terminate) | `backend/app/engine/automationedge_client.py` |
| Node handler (submit + suspend) | `backend/app/engine/node_handlers.py::_handle_automation_edge` |
| Control-flow exception | `backend/app/engine/exceptions.py::NodeSuspendedAsync` |
| Integration config resolver (node → tenant_integrations overlay) | `backend/app/engine/integration_resolver.py` |
| Beat poll state machine (timeout, Diverted transitions, context patch shape) | `backend/app/engine/async_job_poller.py` |
| Shared finalise-and-resume helper | `backend/app/engine/async_job_finalizer.py` |
| Beat task itself | `backend/app/workers/scheduler.py::poll_async_jobs` |
| Webhook endpoint | `backend/app/api/async_jobs.py` |
| CRUD for tenant_integrations | `backend/app/api/tenant_integrations.py` |
| List-async-jobs for the UI | `backend/app/api/workflows.py::list_instance_async_jobs` |
| Schema | Alembic `0017_async_jobs_and_tenant_integrations.py`; models in `app/models/workflow.py` (`AsyncJob`, `TenantIntegration`) |
| Registry | `shared/node_registry.json` (`automation_edge` entry) |
| UI — cyan waiting badge | `frontend/src/components/toolbar/ExecutionPanel.tsx` + `frontend/src/lib/asyncJob.ts` |
| UI — integrations admin | `frontend/src/components/toolbar/IntegrationsDialog.tsx` |

---

## 10. Known limitations (v1)

- **Cancellation is best-effort on the AE side** (§7). Real termination requires AE 5.4+ with a terminate endpoint.
- **Sub-workflows cannot contain AutomationEdge nodes** — same limitation as HITL; an `NodeSuspendedAsync` inside a child workflow is rejected by `_handle_sub_workflow`.
- **Single AE deployment per node**: multi-AE fan-out (submit to N AE instances, merge) is not a node-level feature. Use a ForEach + AutomationEdge combination if needed.
- **No per-job retries** on transient AE 5xx errors. The Beat poller just stamps `last_error` and retries on the next cadence. Transient errors eventually time out if AE doesn't recover before `timeoutSeconds`.

These are tracked as AE-follow-up tickets. File issues against the feature if any bite during real-world use.
