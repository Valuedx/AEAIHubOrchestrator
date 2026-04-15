---
name: Sherlock Mode Implementation Plan
overview: Implement "Sherlock Mode", an interactive investigation loop where the AI proposes read-only actions (SSH, K8s logs) and the user confirms them via chat, leveraging existing chat, action, and streaming services.
todos:
  - id: create-investigation-service
    content: Create `backend/app/services/investigation_service.py` with `InvestigationService` class and `start_investigation`/`process_turn` methods.
    status: completed
  - id: implement-sherlock-agent
    content: Implement `SherlockAgent` logic within `InvestigationService` to handle LLM prompting and tool selection.
    status: completed
    dependencies:
      - create-investigation-service
  - id: register-safe-actions
    content: Register `sherlock.run_shell` and `sherlock.get_k8s_logs` actions in `backend/app/actions/registry.py` (or equivalent definitions).
    status: completed
  - id: implement-command-validation
    content: Implement `_validate_read_only_command` security check in `backend/app/actions/ssh_runner.py` or `ActionExecutor`.
    status: completed
    dependencies:
      - register-safe-actions
  - id: update-chat-routing
    content: Update `backend/app/services/chat_service.py` to handle "Debug incident <ID>" trigger and route to `InvestigationService`.
    status: completed
    dependencies:
      - create-investigation-service
  - id: handle-action-feedback
    content: Modify `_handle_action_confirmation` in `chat_service.py` to feed action results back to `InvestigationService` when in investigation mode.
    status: completed
    dependencies:
      - update-chat-routing
  - id: update-documentation
    content: Update technical documentation to include Sherlock Mode architecture, usage guide, and security boundaries.
    status: completed
    dependencies:
      - handle-action-feedback
---

# Sherlock Mode (Interactive Investigation) Implementation Plan

## 1. Backend Architecture

### A. Investigation Service (`backend/app/services/investigation_service.py`)

Create a new service to manage the multi-turn investigation state.

-   **`start_investigation(incident_id, conversation_id)`**: Initializes state in `ChatConversation.context`.
-   **`process_turn(conversation_id, user_message)`**: The core loop.
    -   Fetches context (history, current incident).
    -   Calls LLM with "Sherlock" system prompt + Read-Only Tools.
    -   Parses response:
        -   If **Action Proposal**: Returns a confirmation request card (reusing `_request_action_confirmation`).
        -   If **Insight**: Streams thought process (reusing `ReasoningStreamService`).

### B. Read-Only Action Definitions

Reuse existing runners but enforce safety.

-   **`backend/app/actions/registry.py`**: Register new "sherlock.*" actions.
    -   `sherlock.run_shell`: Wraps `SSHRunner`, allows only allow-listed commands (`top`, `ps`, `netstat`, `grep`, `tail`, `cat`).
    -   `sherlock.get_k8s_logs`: Wraps `k8s.get_logs`.
-   **Safety Layer**: Implement `_validate_read_only_command(cmd)` in `ActionExecutor` or the specific runner to reject `rm`, `kill`, `shutdown`, etc., at the code level.

### C. Chat Service Integration (`backend/app/services/chat_service.py`)

-   **Mode Switching**: Add logic to `process_natural_chat` to check `context.get("mode") == "investigation"`.
-   **Routing**: If in investigation mode, route to `InvestigationService.process_turn` instead of the standard RAG/Intent flow.
-   **Trigger**: "Debug incident <ID>" command transitions mode to `investigation`.

## 2. Frontend / Channel Interaction

Leverage existing Slack/Teams connectors.

-   **Approval Card**: Reuse `_request_action_confirmation` logic.
    -   Sherlock proposes: "I want to run `tail -n 50 /var/log/syslog` on web-01."
    -   User sees standard card: "Action: Run Shell", "Command: tail...", "Risk: Low".
    -   User clicks "Approve".
-   **Output**:
    -   Action result (`stdout`) is fed back into `InvestigationService.process_turn` as a "tool output" message.
    -   Sherlock analyzes the output and proposes the next step or provides a conclusion.

## 3. Implementation Steps

1.  **Create `InvestigationService`**:

    -   Define `SherlockAgent` class (LLM prompt management).
    -   Implement `process_turn` logic.

2.  **Register Safe Tools**:

    -   Add `sherlock.run_shell` and `sherlock.get_k8s_logs` to `action_registry`.
    -   Implement command validation in `ActionExecutor` or `SSHRunner`.

3.  **Update `ChatService`**:

    -   Add "Debug Mode" toggle logic.
    -   Route "Debug incident X" to `InvestigationService.start_investigation`.
    -   Route subsequent messages in that conversation to `InvestigationService.process_turn`.

4.  **Handle Action Feedback**:

    -   Modify `_handle_action_confirmation` to check if the action was triggered by Sherlock.
    -   If so, instead of just "Done", feed the `action_execution.result` back into `InvestigationService` to trigger the next AI turn.

## 4. Verification

-   **Unit Test**: Test `validate_read_only_command` with malicious inputs (`rm -rf`, `; kill`).
-   **Integration Test**: Mock an incident, start debug mode, verify LLM proposes a command, verify confirmation card appears, verify execution output is processed by LLM.

## 5. Documentation

-   Update API docs and user guide to include "Sherlock Mode" usage and safety features.