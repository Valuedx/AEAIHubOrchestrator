---
name: proactive-self-heal-plan
overview: Add proactive alert-stream listening and predictive remediation with approvals across Slack/Teams and web console, leveraging existing Datadog/ELK connectors, with auto-resolved incident writeback.
todos:
  - id: audit-connectors
    content: Confirm connector APIs for datadog/elk/webhook and gaps
    status: completed
  - id: backend-alert-stream
    content: Design monitor_alert_stream and predictive remediation flow
    status: completed
  - id: backend-writeback-preventive
    content: Implement ITSM writeback for preventive incidents (ServiceNow/ManageEngine)
    status: completed
  - id: frontend-proactive-ui
    content: Design UI for proactive alerts and connector form updates
    status: completed
  - id: approvals-chatops
    content: Map Slack/Teams approval path and callbacks
    status: completed
---

# Proactive Self-Healing Implementation Plan

## Feasibility snapshot

- Connectors present: `ConnectorType` includes Slack/Teams/Datadog/Elasticsearch/Webhook (`backend/app/models/connector.py`). Slack/Teams connectors are implemented and used for ChatOps messaging (`backend/app/connectors/slack.py`, `backend/app/connectors/teams.py`). Datadog connector exists with alert polling (`backend/app/connectors/datadog.py`, `_sync_datadog` in `backend/app/tasks/connector_tasks.py`). ELK connector and log telemetry services are already in place (`backend/app/connectors/elasticsearch.py`, `backend/app/services/log_telemetry_service.py`). Webhook runner provides a custom log/notification channel (`backend/app/actions/runners/webhook_runner.py`). Frontend connectors UI currently omits Elasticsearch and has basic Webhook fields (`frontend/src/pages/Connectors.tsx`).
- ITSM Writeback: `WriteBackService` supports ServiceNow/ManageEngine. `Incident` model allows creating tickets. Best practice selected: Create "Auto-Resolved" Preventive Incidents to demonstrate value (Incidents Prevented metrics).

## Backend

- Add a Celery task `monitor_alert_stream` to consume Datadog alerts in near real time (support both webhook ingestion via `receive_webhook_alert` in `backend/app/api/v1/endpoints/alerts.py` and scheduled polling fallback). Emit SSE events for proactive warnings via `broadcast_incident_update` (`backend/app/api/v1/endpoints/sse.py`).
- Extend alert processing to classify warning-level alerts for predictive risk: enrich with Datadog metrics (existing `TelemetryService`) and logs via ELK (`LogTelemetryService`), and hand off to `RemediationAdvisor` to propose preventive runbooks.
- Create approval flow for preventive actions: persist a `predicted_incident`/`preventive_action` record (using `Prediction` model or similar), and send interactive messages to Slack/Teams using existing connectors (reuse `ExecutionStatusService`/`ReasoningStreamService` patterns). Include a timeout/auto-expire policy.
- Add execution path: when user approves (from Slack/Teams webhook endpoints or web UI):

    1. Trigger cleanup runbook (Edge runner or action engine).
    2. Log execution result.
    3. **Writeback**: Create an Incident in ITSM (ServiceNow/ManageEngine) with status "Resolved", category "Preventive/Auto-Heal", and work notes detailing the action taken and outage averted.
    4. Mark alert as mitigated to avoid duplicate runs.

- Guardrails: tenant scoping, connector availability checks, idempotency for repeated alerts, circuit breakers for Datadog/ELK calls, and audit trails for approvals.

## Frontend

- Connectors UI: add Elasticsearch connector type and richer Webhook/custom log channel fields (URL, auth, index pattern) to `frontend/src/pages/Connectors.tsx`; surface test-connection flows via existing `connectorsApi` endpoints.
- Proactive alerts view: new page/section showing incoming warning-level alerts with predicted time-to-failure, suggested remediation, status (awaiting approval/running/completed), and action buttons. Subscribe to SSE `alert`/`preventive_action` events using `useAgentStream`.
- Slack/Teams parity: display pending approvals and their state in web console (for cases approvals aren’t taken in chat), and allow approve/deny from UI with optimistic updates.
- Notifications: highlight mitigated vs escalated items; link back to related connectors/incidents.

## Deliverables & validation

- Unit tests for alert classification, remediation suggestion, and webhook signature paths; integration test for Slack/Teams approval callback to execution trigger.
- Manual test matrix: Datadog warning → proactive suggestion → Slack/Teams approval → cleanup executed → preventive incident created in ITSM → no outage; webhook-only path; polling fallback; ELK-unavailable degradation; UI SSE updates.