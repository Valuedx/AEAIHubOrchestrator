---
name: RCA Diagnostic Enhancements
overview: Integrate the diagnostic agent bundle's metadata-first, evidence-pack-based diagnosis pipeline into the existing AEAgenticSupport codebase, adding the missing log processing tools, step timeline retrieval, structured evidence pack builder, and enhanced LLM diagnosis flow -- while reusing what already exists.
todos:
  - id: create-diagnostic-tools
    content: Create tools/ae_diagnostic_tools.py with all log processing functions (time window, request ID filter, step filter, exception chain, line collapse, timestamp normalize, merge streams, evidence pack builder) plus tool registrations
    status: completed
  - id: add-step-timeline
    content: Add get_workflow_step_timeline() and get_normalized_instance_metadata() to AutomationEdgeClient in tools/automationedge_client.py
    status: completed
  - id: add-diagnosis-function
    content: Add diagnose_from_evidence_pack() function with structured JSON output (confidence, alternatives, reasoning) using LLM
    status: completed
  - id: register-tools
    content: Register new tools in tool registry and update tools/__init__.py to import the new module
    status: completed
  - id: wire-diagnostic-agent
    content: Update DiagnosticAgent allowed_categories to include new diagnostic tools
    status: completed
isProject: false
---

# RCA Diagnostic Enhancement Plan

## Gap Analysis Summary

The bundle defines 15 tools. Here is the overlap status with the current codebase:

**Already Exists (reuse as-is):**

- `ae_generate_log_bundle` -- covered by `AutomationEdgeClient.request_debug_logs()` / `request_agent_debug_logs()`
- `ae_download_log_bundle` -- covered by `get_agent_debug_logs()` + `get_debug_log_request()` + ZIP extraction in `analyze_agent_logs`
- `ae_read_log_file` -- done inline inside `analyze_agent_logs`

**Partially Exists (enhance):**

- `ae_get_workflow_instance_metadata` -- `get_execution_status()` and `get_workflow_instance_by_id()` fetch raw data but do NOT normalize into the diagnostic shape (failure_step, bot_machine, etc.)
- `ae_extract_error_blocks` -- exists in `[tools/log_tools.py](tools/log_tools.py)` and `[tools/agent_debug_tools.py](tools/agent_debug_tools.py)`, but lacks step-name extraction and `Caused by:` chain following
- `ae_collapse_repeated_log_lines` -- `_group_errors()` in `agent_debug_tools.py` groups at error-block level, but raw line-level dedup is missing

**Completely Missing (new):**

- `ae_get_workflow_step_timeline` -- no step-level timeline fetching at all
- `ae_resolve_log_sources` -- no deterministic log source resolver
- `ae_extract_log_time_window` -- no time-window filtering
- `ae_extract_log_by_request_id` -- no request-ID-based line filtering
- `ae_extract_log_by_step_name` -- no step-name-based line filtering
- `ae_extract_exception_chain` -- no Java exception chain extraction
- `ae_normalize_log_timestamps` -- no timestamp normalization
- `ae_merge_log_streams_chronologically` -- multiple log files processed independently
- `ae_build_log_evidence_pack` -- no structured evidence pack concept at all

**Architecturally Missing:**

- Metadata-first diagnosis flow (current code goes straight to logs)
- Evidence Pack as structured intermediate representation
- Diagnosis prompt with confidence scoring and alternative hypotheses
- Evidence widening strategy (if weak evidence, broaden search)

---

## Implementation Plan

### Phase 1: New module `tools/ae_diagnostic_tools.py` -- Log Evidence Processing Pipeline

Create a new file `[tools/ae_diagnostic_tools.py](tools/ae_diagnostic_tools.py)` containing the **pure local-processing functions** from the bundle. These are stateless text-processing utilities that operate on log text already retrieved by the existing client. No AE API calls.

Functions to add (ported from the bundle's `ae_diagnostic_tools.py`, adapted to use existing timestamp patterns from `agent_debug_tools.py`):

- `ae_extract_log_time_window(log_text, start_time, end_time)` -- keep only lines within failure +/- window
- `ae_extract_log_by_request_id(log_text, request_id)` -- keep lines matching request ID + context
- `ae_extract_log_by_step_name(log_text, step_name)` -- keep lines matching step name + context
- `ae_extract_exception_chain(log_text)` -- extract Java exception type, message, Caused-by chain, top stack frames
- `ae_collapse_repeated_log_lines(log_text)` -- collapse spammy repeated lines, preserving first N occurrences
- `ae_normalize_log_timestamps(log_text)` -- normalize to consistent ISO-8601 UTC
- `ae_merge_log_streams_chronologically(sources)` -- merge multiple named log streams into one sorted timeline
- `ae_build_log_evidence_pack(instance_metadata, step_timeline, ...)` -- the key function that builds the compact LLM-ready evidence payload

The timestamp parsing will be taken from the bundle (`parse_timestamp_from_line`) but extended with the existing `TIMESTAMP_RE` pattern from `agent_debug_tools.py` to handle the AE-specific `2026-03-06T09:48:21.806+05:30` format already known to work in production.

### Phase 2: Step Timeline in `AutomationEdgeClient`

Add `get_workflow_step_timeline(instance_id)` to `[tools/automationedge_client.py](tools/automationedge_client.py)`.

This probes AE endpoints like:

- `/workflowinstances/{id}/steps`
- `/workflowinstances/{id}/timeline`
- `/{org}/workflowinstances/{id}/steps`

Returns a normalized dict: `{ instance_id, failed_step, steps: [{ sequence, step_name, status, start_time, end_time, retry_count }] }`

Also add `get_normalized_instance_metadata(instance_id)` that wraps `get_execution_status()` and normalizes the result into the diagnostic schema: `{ instance_id, workflow_name, status, failure_step, error_message, start_time, end_time, bot_machine }`.

### Phase 3: Register New Tools

Register these as callable tools in the tool registry so the orchestrator and agents can use them. Key new tool registrations in `[tools/ae_diagnostic_tools.py](tools/ae_diagnostic_tools.py)`:

- `build_evidence_pack` -- high-level tool that composes the full pipeline: fetch metadata + step timeline + logs, then runs evidence reduction, and outputs a structured evidence pack
- `extract_exception_chain` -- standalone tool for exception chain analysis from existing log text

### Phase 4: Enhanced Diagnosis Prompt and Flow

Add a diagnosis function that takes an evidence pack and produces a structured JSON diagnosis with:

- `primary_diagnosis` (string)
- `confidence` (0.0-1.0)
- `alternatives` (list)
- `reasoning_summary` (string)
- `recommended_next_fetch` (list)
- `safe_remediation_candidates` (list)

This goes in a new function `diagnose_from_evidence_pack()` in `tools/ae_diagnostic_tools.py`, using `llm_client.chat()` with the bundle's recommended system prompt.

### Phase 5: Wire into DiagnosticAgent and Orchestrator

Update `[agents/diagnostic_agent.py](agents/diagnostic_agent.py)` `allowed_categories` to include the new `"diagnostics"` category for the evidence pack tools. The orchestrator's tool selection (via RAG ranking) will naturally pick up the new tools when appropriate.

---

## Key Design Decisions

- **New file, not a copy**: We do NOT copy the bundle's `ae_diagnostic_tools.py` wholesale. We create a new `tools/ae_diagnostic_tools.py` that integrates with the existing project's patterns (tool registry, `get_ae_client()`, existing AE client, existing timestamp formats).
- **Reuse existing AE client**: The bundle provides its own `AEClient` class -- we ignore it entirely and use the existing `AutomationEdgeClient` which already handles auth, path probing, retries, and ZIP extraction.
- **Additive, not disruptive**: All existing tools (`get_execution_logs`, `analyze_agent_logs`, `generate_rca_report`) continue working unchanged. The new tools provide a richer diagnosis path that the agent can choose when appropriate.
- **Evidence pack feeds into RCA**: The `RCAAgent` can optionally consume an evidence pack to produce higher-quality RCA reports, but this is an enhancement -- not a rewrite.

---

## Files to Create/Modify


| File                             | Action     | What                                                                                      |
| -------------------------------- | ---------- | ----------------------------------------------------------------------------------------- |
| `tools/ae_diagnostic_tools.py`   | **CREATE** | Log processing pipeline + evidence pack builder + diagnosis function + tool registrations |
| `tools/automationedge_client.py` | **MODIFY** | Add `get_workflow_step_timeline()` and `get_normalized_instance_metadata()`               |
| `tools/__init__.py`              | **MODIFY** | Add import for new module                                                                 |
| `agents/diagnostic_agent.py`     | **MODIFY** | Add `"diagnostics"` to allowed_categories                                                 |


