---
name: "Plan: User-facing Interfaces for Problem RCA"
overview: ""
todos:
  - id: backend-connectors
    content: Update ServiceNow and ManageEngine connectors to support fetching/listing problems
    status: completed
  - id: backend-endpoints
    content: Add GET and POST (trigger RCA) endpoints to problems.py
    status: completed
  - id: frontend-api
    content: Update frontend api.ts with problemsApi
    status: completed
  - id: frontend-ui
    content: Create Problems.tsx page with list and RCA detail view
    status: completed
---

# Plan: User-facing Interfaces for Problem RCA

## Backend Changes

### 1. Add Problem Management Endpoints

**File:** `backend/app/api/v1/endpoints/problems.py`

-   Add `GET /{problem_id}` endpoint:
    -   Fetches problem details from ITSM (ServiceNow/ManageEngine) using the connector.
    -   Include RCA analysis data if available (parse from work notes/custom fields, similar to the CrewAI tool logic).
-   Add `GET /` endpoint (List Problems):
    -   Fetches list of problems from ITSM.
-   Add `POST /{problem_id}/rca` endpoint:
    -   Triggers `analyze_problem_rca_task` manually.
    -   Accepts `incident_ids` in body (optional, can try to fetch from problem if not provided).

### 2. Update Connectors

**Files:** `backend/app/connectors/servicenow.py`, `backend/app/connectors/manageengine.py`

-   Ensure `get_problem(id)` and `list_problems(limit, offset)` methods exist.
-   If not, implement them following the `get_incidents` pattern.

## Frontend Changes

### 1. Update API Service

**File:** `frontend/src/services/api.ts`

-   Add `problemsApi` object with:
    -   `list()`
    -   `get(id)`
    -   `create(data)` (already exists as `problemsApi.create` but update if needed)
    -   `triggerRCA(id, data)`

### 2. Create Problems Page

**File:** `frontend/src/pages/Problems.tsx`

-   Create a new page component similar to `Incidents.tsx`.
-   Implement a DataGrid to list problems.
-   Columns: ID, Title, Priority, State, RCA Status.
-   Actions: View Details.

### 3. Implement Problem Detail Drawer

-   Inside `Problems.tsx` (or a separate component `ProblemDetail.tsx` if preferred, but inline is consistent with `Incidents.tsx`).
-   Tabs:
    -   **Overview**: Basic info (Description, ITSM link).
    -   **RCA**: Display Root Cause, Confidence, Evidence, Contributing Factors.
        -   Use visual indicators for confidence (e.g., progress bar).
        -   List evidence and contributing factors clearly.
        -   Show "Pattern Analysis" (common keywords, temporal patterns).
    -   **Related Incidents**: List of incidents linked to the problem.
-   **Action Buttons**:
    -   "Trigger RCA Analysis" (if not enriched or to re-run).
    -   "Update Problem" (link to ITSM or simple status update).

## Execution Steps

1.  **Backend**: Check and update connectors (`servicenow.py`, `manageengine.py`) for problem fetching.
2.  **Backend**: Update `problems.py` with GET and POST (trigger RCA) endpoints.
3.  **Frontend**: Update `api.ts`.
4.  **Frontend**: Create `Problems.tsx` with list and detail view (including RCA visualization).