import { openSSE } from "@/lib/sse";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8001";
const TENANT_ID = import.meta.env.VITE_TENANT_ID || "default";

// Auth token storage key. We use sessionStorage (not localStorage) so the
// token does not survive a browser restart and is scoped to the tab — this
// meaningfully reduces XSS blast radius.
export const AUTH_TOKEN_KEY = "ae_access_token";

export function getAuthToken(): string | null {
  return sessionStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  // Best-effort cleanup in case the previous version stashed a token here.
  try { localStorage.removeItem(AUTH_TOKEN_KEY); } catch { /* ignore */ }
}

export function clearAuthToken(): void {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  try { localStorage.removeItem(AUTH_TOKEN_KEY); } catch { /* ignore */ }
}

/** Thrown on non-2xx responses; includes parsed JSON body when possible (e.g. sync 504 with instance_id). */
export class ApiError extends Error {
  readonly status: number;
  readonly bodyText: string;
  readonly json: Record<string, unknown> | undefined;

  constructor(message: string, status: number, bodyText: string, json?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.bodyText = bodyText;
    this.json = json;
  }
}

function getAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  if (token) return { "Authorization": `Bearer ${token}` };
  return { "X-Tenant-Id": TENANT_ID };
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...options.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let json: Record<string, unknown> | undefined;
    try {
      const parsed: unknown = JSON.parse(body);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        json = parsed as Record<string, unknown>;
      }
    } catch {
      /* plain text body */
    }
    throw new ApiError(`API ${res.status}: ${body}`, res.status, body, json);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types matching backend Pydantic schemas
// ---------------------------------------------------------------------------

export interface WorkflowOut {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  graph_json: { nodes: unknown[]; edges: unknown[] };
  version: number;
  /** DV-07 — when false, Schedule Triggers are suspended for this
   *  workflow. Manual Run and PATCH still work. */
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface InstanceOut {
  id: string;
  tenant_id: string;
  workflow_def_id: string;
  status: string;
  current_node_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  /** WorkflowDefinition.version when this run was queued (null for older instances). */
  definition_version_at_start?: number | null;
  parent_instance_id?: string | null;
  parent_node_id?: string | null;
  /** Distinguishes HITL-suspended (null) from async-external-suspended
   *  ('async_external'). Drives the ExecutionPanel UI between the
   *  Review dialog and the cyan waiting-on-external badge. */
  suspended_reason?: string | null;
}

/** Response when `sync: true` on execute — HTTP 200. */
export interface SyncExecuteOut {
  instance_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  output: Record<string, unknown>;
}

export interface ExecutionLogOut {
  id: string;
  instance_id: string;
  node_id: string;
  node_type: string;
  status: string;
  input_json: Record<string, unknown> | null;
  output_json: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ChildInstanceSummary {
  id: string;
  workflow_def_id: string;
  workflow_name: string | null;
  parent_node_id: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface InstanceDetailOut extends InstanceOut {
  logs: ExecutionLogOut[];
  children?: ChildInstanceSummary[];
}

export interface ToolOut {
  name: string;
  title: string;
  description: string;
  category: string;
  safety_tier: string;
  tags: string[];
}

export interface ConversationMessageOut {
  role: string;
  content: string;
  timestamp: string | null;
}

export interface ConversationSessionSummaryOut {
  session_id: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationSessionOut {
  session_id: string;
  tenant_id: string;
  messages: ConversationMessageOut[];
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationEpisodeOut {
  id: string;
  session_id: string;
  status: string;
  start_turn: number;
  end_turn: number | null;
  title: string | null;
  checkpoint_summary_text: string | null;
  summary_through_turn: number;
  archive_reason: string | null;
  last_activity_at: string;
  archived_at: string | null;
  archived_memory_record_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ArchiveConversationEpisodeOut {
  session_id: string;
  archived: boolean;
  episode_id: string | null;
  title: string | null;
  archive_reason: string | null;
  archived_at: string | null;
  memory_record_ids: string[];
  memory_records_created: number;
  summary_text: string;
}

export interface MemoryProfileOut {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  workflow_def_id: string | null;
  is_default: boolean;
  instructions_text: string | null;
  enabled_scopes: string[];
  max_recent_tokens: number;
  max_semantic_hits: number;
  include_entity_memory: boolean;
  summary_trigger_messages: number;
  summary_recent_turns: number;
  summary_max_tokens: number;
  summary_provider: string;
  summary_model: string;
  episode_archive_provider: string;
  episode_archive_model: string;
  episode_inactivity_minutes: number;
  episode_min_turns: number;
  auto_archive_on_resolved: boolean;
  promote_interactions: boolean;
  history_order: "summary_first" | "recent_first";
  semantic_score_threshold: number;
  embedding_provider: string;
  embedding_model: string;
  vector_store: string;
  entity_mappings_json: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
}

export interface MemoryRecordOut {
  id: string;
  tenant_id: string;
  scope: string;
  scope_key: string;
  kind: string;
  content: string;
  metadata_json: Record<string, unknown>;
  session_ref_id: string | null;
  workflow_def_id: string | null;
  entity_type: string | null;
  entity_key: string | null;
  source_instance_id: string | null;
  source_node_id: string | null;
  embedding_provider: string;
  embedding_model: string;
  vector_store: string;
  created_at: string;
}

export interface EntityFactOut {
  id: string;
  tenant_id: string;
  entity_type: string;
  entity_key: string;
  fact_name: string;
  fact_value: string;
  confidence: number;
  valid_from: string;
  valid_to: string | null;
  superseded_by: string | null;
  session_ref_id: string | null;
  workflow_def_id: string | null;
  source_instance_id: string | null;
  source_node_id: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface ResolvedMemoryLogOut {
  node_id: string;
  node_type: string;
  completed_at: string | null;
  memory_debug: Record<string, unknown>;
  recent_turns: Array<Record<string, unknown>>;
  entity_facts: EntityFactOut[];
  memory_records: MemoryRecordOut[];
}

export interface SnapshotOut {
  id: string;
  workflow_def_id: string;
  version: number;
  saved_at: string;
}

export interface SnapshotDetailOut extends SnapshotOut {
  graph_json: { nodes: unknown[]; edges: unknown[] };
}

export interface CheckpointOut {
  id: string;
  instance_id: string;
  node_id: string;
  saved_at: string | null;
}

export interface CheckpointDetailOut extends CheckpointOut {
  context_json: Record<string, unknown>;
}

export interface InstanceContextOut {
  instance_id: string;
  status: string;
  current_node_id: string | null;
  /** approvalMessage extracted from the suspended node config */
  approval_message: string | null;
  /** execution context with internal (_-prefixed) keys stripped */
  context_json: Record<string, unknown>;
}

// HITL-01.a — one row from the approval_audit_log table, exposed
// via GET /workflows/{id}/instances/{id}/approvals. Each row
// captures one approve / reject / timeout_rejected / timeout_escalated
// event for compliance export + future dashboard drill-in.
export type ApprovalDecision =
  | "approved"
  | "rejected"
  | "timeout_rejected"
  | "timeout_escalated";

export interface ApprovalAuditEntry {
  id: string;
  instance_id: string;
  node_id: string;
  /** Reserved for HITL-01.f bubble-up — null on all v0.a rows. */
  parent_instance_id: string | null;
  /** Claimed identity. Protected today only by tenant-scoped
   *  bearer auth. Treat as attested, not cryptographically
   *  verified, until OIDC integration lands. */
  approver: string;
  decision: ApprovalDecision;
  reason: string | null;
  /** Context snapshot immediately before the patch merge — what
   *  the approver was looking at when they decided. */
  context_before_json: Record<string, unknown> | null;
  /** Post-merge snapshot — includes approval_payload under
   *  "approval" + any keys from context_patch. */
  context_after_json: Record<string, unknown> | null;
  /** Reserved for HITL-01.d allowlist enforcement — null on v0.a rows. */
  approvers_allowlist_matched: boolean | null;
  created_at: string;
}

// Knowledge Base types
export interface KBOut {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimension: number;
  vector_store: string;
  chunking_strategy: string;
  chunk_size: number;
  chunk_overlap: number;
  semantic_threshold: number | null;
  document_count: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface KBDocumentOut {
  id: string;
  kb_id: string;
  filename: string;
  content_type: string;
  file_size: number;
  chunk_count: number;
  status: string;
  error: string | null;
  created_at: string;
}

export interface EmbeddingOption {
  provider: string;
  model: string;
  dimension: number;
}

export interface ChunkingStrategy {
  id: string;
  label: string;
  description: string;
}

export interface VectorStoreOption {
  id: string;
  label: string;
  description: string;
}

// DV-02 — response shape from POST /workflows/{id}/nodes/{node_id}/test
export interface TestNodeResponse {
  output: Record<string, unknown> | null;
  elapsed_ms: number;
  error: string | null;
}

// Async external jobs (AutomationEdge, future Jenkins, ...) — AE-07
export interface AsyncJobOut {
  id: string;
  instance_id: string;
  node_id: string;
  system: string;
  external_job_id: string;
  status: string;                        // submitted | running | completed | failed | cancelled | timed_out
  submitted_at: string;
  last_polled_at: string | null;
  completed_at: string | null;
  last_external_status: string | null;   // AE's own status ('Executing', 'Diverted', 'Complete', ...)
  total_diverted_ms: number;
  diverted_since: string | null;
  last_error: string | null;
}

// Tenant Secrets
export interface SecretOut {
  id: string;
  key_name: string;
  created_at: string;
  updated_at: string;
}

// Tenant Integrations — per-tenant connection defaults for external systems
// (AutomationEdge, future Jenkins/Temporal). See AE-06.
export interface TenantIntegrationOut {
  id: string;
  tenant_id: string;
  system: string;
  label: string;
  config_json: Record<string, unknown>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface TenantIntegrationCreate {
  system: string;
  label: string;
  config_json: Record<string, unknown>;
  is_default?: boolean;
}

export interface TenantIntegrationUpdate {
  label?: string;
  config_json?: Record<string, unknown>;
  is_default?: boolean;
}

// MCP-02 — per-tenant MCP server registry.
// ``auth_mode``:
//   * 'none'           — no auth
//   * 'static_headers' — config_json.headers with {{ env.KEY }} placeholders
//   * 'oauth_2_1'      — reserved (MCP-03); API accepts, runtime rejects
export type McpAuthMode = "none" | "static_headers" | "oauth_2_1";

export interface TenantMcpServerOut {
  id: string;
  tenant_id: string;
  label: string;
  url: string;
  auth_mode: McpAuthMode;
  config_json: Record<string, unknown>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface TenantMcpServerCreate {
  label: string;
  url: string;
  auth_mode?: McpAuthMode;
  config_json?: Record<string, unknown>;
  is_default?: boolean;
}

export interface TenantMcpServerUpdate {
  label?: string;
  url?: string;
  auth_mode?: McpAuthMode;
  config_json?: Record<string, unknown>;
  is_default?: boolean;
}

// ADMIN-01 — per-tenant policy overrides.
//
// A tenant has exactly one policy row (keyed by tenant_id, not a UUID).
// ``values`` are the EFFECTIVE values (override if set; env default
// otherwise). ``source`` names where each field actually came from so
// the UI can show operators which knobs are inherited vs. overridden.
export type TenantPolicySource = "tenant_policy" | "env_default";

export interface TenantPolicyOut {
  tenant_id: string;
  values: {
    execution_quota_per_hour: number;
    max_snapshots: number;
    mcp_pool_size: number;
    // ADMIN-02
    rate_limit_requests_per_window: number;
    rate_limit_window_seconds: number;
  };
  // SMART-XX feature flags. Each is an opt-out toggle so cost-
  // conscious tenants can disable subsets. SMART-04 + SMART-06 ship
  // today; the rest of the SMART-XX series adds keys here as they
  // land.
  flags: {
    smart_04_lints_enabled: boolean;
    smart_06_mcp_discovery_enabled: boolean;
    smart_02_pattern_library_enabled: boolean;
    // SMART-01 — scenario memory + strict promote-gate. Both
    // default off (opt-in) because they spend engine tokens.
    smart_01_scenario_memory_enabled: boolean;
    smart_01_strict_promote_gate_enabled: boolean;
  };
  source: {
    execution_quota_per_hour: TenantPolicySource;
    max_snapshots: TenantPolicySource;
    mcp_pool_size: TenantPolicySource;
    rate_limit_requests_per_window: TenantPolicySource;
    rate_limit_window_seconds: TenantPolicySource;
    // SMART-XX flags
    smart_04_lints_enabled: TenantPolicySource;
    smart_06_mcp_discovery_enabled: TenantPolicySource;
    smart_02_pattern_library_enabled: TenantPolicySource;
    smart_01_scenario_memory_enabled: TenantPolicySource;
    smart_01_strict_promote_gate_enabled: TenantPolicySource;
  };
  updated_at: string | null;
}

// ADMIN-03 — per-tenant LLM provider credentials (read-only status).
//
// Shape mirrors backend app/api/llm_credentials.py. The server never
// returns the secret values — only source labels so the dialog can
// render "tenant override" / "env default" / "not configured" badges.
export type LlmCredentialSource = "tenant_secret" | "env_default" | "missing";

export interface LlmCredentialStatus {
  source: LlmCredentialSource;
  secret_name: string;
}

export interface LlmCredentialsOut {
  tenant_id: string;
  providers: {
    google: LlmCredentialStatus;
    openai: LlmCredentialStatus;
    openai_base_url: LlmCredentialStatus;
    anthropic: LlmCredentialStatus;
  };
}

// STARTUP-01 — preflight readiness probe.
export type StartupCheckStatus = "pass" | "warn" | "fail";

export interface StartupCheck {
  name: string;
  status: StartupCheckStatus;
  message: string;
  remediation: string;
  details?: Record<string, unknown>;
}

export interface HealthReadyOut {
  // Aggregate: "fail" if any check failed, "warn" if any warned, else "pass".
  status: StartupCheckStatus;
  checks: StartupCheck[];
}

// PATCH body: each field is optional.
// * ``undefined`` (field omitted)  → leave prior override alone
// * ``null``                       → clear override, fall through to env default
// * ``number``                     → set / overwrite override
export interface TenantPolicyUpdate {
  execution_quota_per_hour?: number | null;
  max_snapshots?: number | null;
  mcp_pool_size?: number | null;
  rate_limit_requests_per_window?: number | null;
  rate_limit_window_seconds?: number | null;
  // SMART-04 — opt-out toggle for the copilot's proactive authoring
  // lints. null clears the override so env default takes over.
  smart_04_lints_enabled?: boolean | null;
  // SMART-06 — opt-out toggle for MCP tool discovery.
  smart_06_mcp_discovery_enabled?: boolean | null;
  // SMART-02 — opt-out toggle for the accepted-patterns library.
  smart_02_pattern_library_enabled?: boolean | null;
  // SMART-01 — auto-save successful execute_draft as regression
  // scenarios (deduped by payload hash). Off by default.
  smart_01_scenario_memory_enabled?: boolean | null;
  // SMART-01 — strict promote-gate: promote refuses with 400 on
  // any failing scenario (no "promote anyway" override). Off by
  // default. When on, the PromoteDialog hides its soft-gate
  // checkbox and hard-blocks Apply until all scenarios pass.
  smart_01_strict_promote_gate_enabled?: boolean | null;
}

export interface KBChunkOut {
  content: string;
  score: number;
  chunk_index: number;
  document_id: string;
  document_filename: string;
  metadata: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// COPILOT-01 — draft workspace + tool-layer types
// ---------------------------------------------------------------------------

export type CopilotToolName =
  | "list_node_types"
  | "get_node_schema"
  | "add_node"
  | "update_node_config"
  | "delete_node"
  | "connect_nodes"
  | "disconnect_edge"
  | "validate_graph"
  // COPILOT-01b.ii runner tools (stateful, touch the engine).
  | "test_node"
  // Deterministic-automation fork — the agent reads existing AE
  // connections + the AE Copilot deep-link URL so it can propose
  // "add an automationedge node here" vs. "jump to AE Copilot to
  // design the RPA first". UI can render the copilot_url in the
  // tool_result event as a clickable deep-link.
  | "get_automationedge_handoff_info"
  // Trial-run the draft end-to-end; returns {instance_id, status,
  // output, elapsed_ms} or {instance_id, status: "timeout", hint}.
  // Creates an ephemeral workflow_definitions row under the hood.
  | "execute_draft"
  // Fetch per-node logs for a prior execute_draft instance.
  // Scoped to copilot-initiated (is_ephemeral=true) runs so the
  // agent can't read production logs.
  | "get_execution_logs"
  // Docs grounding (01b.iii) — file-backed word-overlap search
  // over codewiki/*.md + flattened node_registry. No vector DB;
  // the agent uses these for concept questions and before
  // proposing a complex config.
  | "search_docs"
  | "get_node_examples"
  // SMART-04 — supersedes validate_graph; returns schema + lints.
  | "check_draft"
  // SMART-06 — list MCP tools the tenant has connected so the
  // agent can propose relevant ones during drafting.
  | "discover_mcp_tools"
  // SMART-02 — recall nearest accepted workflow patterns this
  // tenant promoted in the past; agent uses them as few-shot
  // instead of synthesising from scratch.
  | "recall_patterns"
  // COPILOT-03.a — persisted test scenarios for the debug loop.
  // Scenarios are draft-scoped; promote migrates them onto the
  // resulting workflow (03.e). `save` creates, `run` re-executes
  // the draft with the stored payload and diffs actual vs
  // expected_output_contains, `list` lets the agent pick one
  // without guessing an id.
  | "save_test_scenario"
  | "run_scenario"
  | "list_scenarios"
  // COPILOT-03.b — ad-hoc debug runs with pins / node config
  // overrides (nothing persisted), plus a per-node error probe
  // that surfaces resolved_config + error text for the failed
  // node. Same ephemeral-only safety gate as get_execution_logs.
  | "run_debug_scenario"
  | "get_node_error"
  // COPILOT-03.c — node-scoped LLM subcall proposing a config
  // patch. NEVER auto-applies; user confirms before the agent
  // calls update_node_config. Per-draft cap of 5 to prevent
  // runaway auto-heal.
  | "suggest_fix";

// SMART-04 — one structured lint finding surfaced by check_draft.
export type CopilotLintSeverity = "error" | "warn";

export interface CopilotLint {
  code: string;
  severity: CopilotLintSeverity;
  message: string;
  fix_hint: string | null;
  node_id: string | null;
}

export interface CopilotDraftValidation {
  errors: string[];
  warnings: string[];
  // SMART-04 additions — present when the tool_result came from
  // `check_draft` (the agent's preferred validator). Absent /
  // empty for `validate_graph` calls.
  lints?: CopilotLint[];
  lints_enabled?: boolean;
}

export interface CopilotDraftOut {
  id: string;
  tenant_id: string;
  base_workflow_id: string | null;
  base_version_at_fork: number | null;
  title: string;
  graph_json: { nodes: unknown[]; edges: unknown[] };
  /** Optimistic-concurrency token — include as expected_version on mutations. */
  version: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  validation: CopilotDraftValidation;
}

export interface CopilotToolCallOut {
  tool: CopilotToolName;
  draft_version: number;
  result: Record<string, unknown>;
  /** Populated on mutation tools; null for read-only tools. */
  validation: CopilotDraftValidation | null;
}

export interface CopilotPromoteOut {
  workflow_id: string;
  version: number;
  /** True = net-new workflow; false = new version of existing. */
  created: boolean;
}

// COPILOT-03.e — scenario list + run-all shapes surfaced by PromoteDialog.
export interface CopilotScenarioOut {
  scenario_id: string;
  name: string;
  payload: Record<string, unknown>;
  has_expected: boolean;
  created_at: string;
}

export type CopilotScenarioStatus = "pass" | "fail" | "stale" | "error";

export interface CopilotScenarioRunOut {
  scenario_id: string;
  name: string;
  status: CopilotScenarioStatus;
  mismatches: Array<Record<string, unknown>>;
  actual_output: Record<string, unknown> | null;
  message: string | null;
}

export interface CopilotScenariosRunAllOut {
  count: number;
  pass_count: number;
  fail_count: number;
  stale_count: number;
  error_count: number;
  results: CopilotScenarioRunOut[];
}

// ---------------------------------------------------------------------------
// COPILOT-01b — session + turn streaming types
// ---------------------------------------------------------------------------

export interface CopilotSessionOut {
  id: string;
  tenant_id: string;
  draft_id: string;
  provider: string;
  model: string;
  /** active | abandoned | completed */
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CopilotTurnOut {
  id: string;
  session_id: string;
  turn_index: number;
  /** user | assistant | tool */
  role: string;
  content_json: Record<string, unknown>;
  tool_calls_json: Record<string, unknown> | Array<Record<string, unknown>> | null;
  token_usage_json: Record<string, unknown> | null;
  created_at: string;
}

export interface CopilotProvidersOut {
  providers: string[];
  default_model: Record<string, string>;
  tools: Array<{
    name: CopilotToolName | string;
    description: string;
    input_schema: Record<string, unknown>;
  }>;
}

/**
 * Every event the agent runner streams over SSE shares this discriminated
 * union shape. See `backend/app/copilot/agent.py` for the authoritative
 * contract.
 */
export type CopilotAgentEvent =
  | { type: "assistant_text"; text: string }
  | {
      type: "tool_call";
      id: string;
      name: CopilotToolName | string;
      args: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      id: string;
      name: CopilotToolName | string;
      result: Record<string, unknown>;
      validation: CopilotDraftValidation | null;
      draft_version: number;
      error: string | null;
    }
  | { type: "error"; message: string; recoverable: boolean }
  | {
      type: "done";
      turns_added: string[];
      final_text: string;
    };

// ---------------------------------------------------------------------------
// Workflow CRUD
// ---------------------------------------------------------------------------

export const api = {
  listWorkflows(): Promise<WorkflowOut[]> {
    return request("/api/v1/workflows");
  },

  getWorkflow(id: string): Promise<WorkflowOut> {
    return request(`/api/v1/workflows/${id}`);
  },

  createWorkflow(body: {
    name: string;
    description?: string;
    graph_json: { nodes: unknown[]; edges: unknown[] };
  }): Promise<WorkflowOut> {
    return request("/api/v1/workflows", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  updateWorkflow(
    id: string,
    body: {
      name?: string;
      description?: string;
      graph_json?: { nodes: unknown[]; edges: unknown[] };
      is_active?: boolean;
    },
  ): Promise<WorkflowOut> {
    return request(`/api/v1/workflows/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deleteWorkflow(id: string): Promise<void> {
    return request(`/api/v1/workflows/${id}`, { method: "DELETE" });
  },

  /** DV-05 — duplicate a workflow definition. */
  duplicateWorkflow(id: string): Promise<WorkflowOut> {
    return request(`/api/v1/workflows/${id}/duplicate`, { method: "POST" });
  },

  // ---------------------------------------------------------------------------
  // Execution
  // ---------------------------------------------------------------------------

  executeWorkflow(
    id: string,
    triggerPayload?: Record<string, unknown>,
    deterministicMode?: boolean,
    sync?: boolean,
    syncTimeout?: number,
  ): Promise<InstanceOut | SyncExecuteOut> {
    return request(`/api/v1/workflows/${id}/execute`, {
      method: "POST",
      body: JSON.stringify({
        trigger_payload: triggerPayload ?? null,
        deterministic_mode: deterministicMode ?? false,
        sync: sync ?? false,
        sync_timeout: syncTimeout ?? 120,
      }),
    });
  },

  callbackWorkflow(
    workflowId: string,
    instanceId: string,
    approvalPayload: Record<string, unknown> = {},
    contextPatch?: Record<string, unknown>,
    options?: {
      approver?: string;
      decision?: "approved" | "rejected";
      reason?: string;
    },
  ): Promise<InstanceOut> {
    return request(`/api/v1/workflows/${workflowId}/instances/${instanceId}/callback`, {
      method: "POST",
      body: JSON.stringify({
        approval_payload: approvalPayload,
        context_patch: contextPatch ?? null,
        // HITL-01.a — claimed identity + decision + reason. All
        // optional on the backend for v0 back-compat, but the
        // dialog always sends them so the audit log has real data.
        approver: options?.approver ?? null,
        decision: options?.decision ?? null,
        reason: options?.reason ?? null,
      }),
    });
  },

  listInstanceApprovals(
    workflowId: string,
    instanceId: string,
  ): Promise<ApprovalAuditEntry[]> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/approvals`,
    );
  },

  getInstanceContext(
    workflowId: string,
    instanceId: string,
  ): Promise<InstanceContextOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/context`,
    );
  },

  retryInstance(
    workflowId: string,
    instanceId: string,
    fromNodeId?: string,
  ): Promise<InstanceOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/retry`,
      {
        method: "POST",
        body: JSON.stringify({ from_node_id: fromNodeId ?? null }),
      },
    );
  },

  /** Cooperative cancel: runner stops after the current node (between nodes). */
  cancelInstance(workflowId: string, instanceId: string): Promise<InstanceOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/cancel`,
      { method: "POST" },
    );
  },

  /** Cooperative pause: runner pauses after the current node (between nodes). */
  pauseInstance(workflowId: string, instanceId: string): Promise<InstanceOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/pause`,
      { method: "POST" },
    );
  },

  /** Resume a run that was paused between nodes (not HITL suspended). */
  resumePausedInstance(
    workflowId: string,
    instanceId: string,
    contextPatch?: Record<string, unknown> | null,
  ): Promise<InstanceOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/resume-paused`,
      {
        method: "POST",
        body: JSON.stringify({ context_patch: contextPatch ?? null }),
      },
    );
  },

  listInstances(workflowId: string): Promise<InstanceOut[]> {
    return request(`/api/v1/workflows/${workflowId}/status`);
  },

  // DV-02 — run just one node in isolation against upstream pins.
  testNode(
    workflowId: string,
    nodeId: string,
    triggerPayload?: Record<string, unknown>,
  ): Promise<TestNodeResponse> {
    return request(
      `/api/v1/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/test`,
      {
        method: "POST",
        body: JSON.stringify({ trigger_payload: triggerPayload ?? null }),
      },
    );
  },

  // DV-01 — pin / unpin a node's output for short-circuit dispatch.
  pinNodeOutput(
    workflowId: string,
    nodeId: string,
    output: Record<string, unknown>,
  ): Promise<WorkflowOut> {
    return request(
      `/api/v1/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/pin`,
      {
        method: "POST",
        body: JSON.stringify({ output }),
      },
    );
  },

  unpinNodeOutput(
    workflowId: string,
    nodeId: string,
  ): Promise<WorkflowOut> {
    return request(
      `/api/v1/workflows/${workflowId}/nodes/${encodeURIComponent(nodeId)}/pin`,
      { method: "DELETE" },
    );
  },

  getInstanceDetail(
    workflowId: string,
    instanceId: string,
  ): Promise<InstanceDetailOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}`,
    );
  },

  listInstanceAsyncJobs(
    workflowId: string,
    instanceId: string,
  ): Promise<AsyncJobOut[]> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/async-jobs`,
    );
  },

  listCheckpoints(
    workflowId: string,
    instanceId: string,
  ): Promise<CheckpointOut[]> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/checkpoints`,
    );
  },

  getCheckpointDetail(
    workflowId: string,
    instanceId: string,
    checkpointId: string,
  ): Promise<CheckpointDetailOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}/checkpoints/${checkpointId}`,
    );
  },

  listTools(): Promise<ToolOut[]> {
    return request("/api/v1/tools");
  },

  listConversationSessions(): Promise<ConversationSessionSummaryOut[]> {
    return request("/api/v1/conversations");
  },

  getConversationSession(sessionId: string): Promise<ConversationSessionOut> {
    return request(`/api/v1/conversations/${sessionId}`);
  },

  listConversationEpisodes(sessionId: string): Promise<ConversationEpisodeOut[]> {
    return request(`/api/v1/conversations/${sessionId}/episodes`);
  },

  archiveConversationEpisode(
    sessionId: string,
    body: {
      reason?: "resolved" | "inactive" | "manual";
      summary_text?: string | null;
      title?: string | null;
      memory_profile_id?: string | null;
    } = {},
  ): Promise<ArchiveConversationEpisodeOut> {
    return request(`/api/v1/conversations/${sessionId}/archive-active-episode`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  deleteConversationSession(sessionId: string): Promise<void> {
    return request(`/api/v1/conversations/${sessionId}`, { method: "DELETE" });
  },

  listMemoryProfiles(): Promise<MemoryProfileOut[]> {
    return request("/api/v1/memory-profiles");
  },

  createMemoryProfile(body: Omit<MemoryProfileOut, "id" | "tenant_id" | "created_at" | "updated_at">): Promise<MemoryProfileOut> {
    return request("/api/v1/memory-profiles", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getMemoryProfile(id: string): Promise<MemoryProfileOut> {
    return request(`/api/v1/memory-profiles/${id}`);
  },

  updateMemoryProfile(
    id: string,
    body: Partial<Omit<MemoryProfileOut, "id" | "tenant_id" | "created_at" | "updated_at">>,
  ): Promise<MemoryProfileOut> {
    return request(`/api/v1/memory-profiles/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  },

  deleteMemoryProfile(id: string): Promise<void> {
    return request(`/api/v1/memory-profiles/${id}`, { method: "DELETE" });
  },

  listMemoryRecords(params: {
    scope?: string;
    scope_key?: string;
    kind?: string;
    entity_type?: string;
    entity_key?: string;
    workflow_def_id?: string;
    source_instance_id?: string;
    limit?: number;
  } = {}): Promise<MemoryRecordOut[]> {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        search.set(key, String(value));
      }
    }
    return request(`/api/v1/memory/records${search.toString() ? `?${search.toString()}` : ""}`);
  },

  listEntityFacts(params: {
    entity_type?: string;
    entity_key?: string;
    include_inactive?: boolean;
    limit?: number;
  } = {}): Promise<EntityFactOut[]> {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        search.set(key, String(value));
      }
    }
    return request(`/api/v1/memory/entity-facts${search.toString() ? `?${search.toString()}` : ""}`);
  },

  resolveInstanceMemory(instanceId: string): Promise<ResolvedMemoryLogOut[]> {
    return request(`/api/v1/memory/instances/${instanceId}/resolved`);
  },

  listVersions(workflowId: string): Promise<SnapshotOut[]> {
    return request(`/api/v1/workflows/${workflowId}/versions`);
  },

  /** Graph JSON for a historical definition version (for checkpoint replay alignment). */
  getGraphAtVersion(
    workflowId: string,
    version: number,
  ): Promise<{ version: number; graph_json: { nodes: unknown[]; edges: unknown[] } }> {
    return request(`/api/v1/workflows/${workflowId}/graph-at-version/${version}`);
  },

  rollbackVersion(workflowId: string, version: number): Promise<WorkflowOut> {
    return request(`/api/v1/workflows/${workflowId}/rollback/${version}`, {
      method: "POST",
    });
  },

  streamInstance(
    workflowId: string,
    instanceId: string,
    onLog: (log: Partial<ExecutionLogOut>) => void,
    onStatus: (status: { instance_status: string; current_node_id?: string | null }) => void,
    onDone: () => void,
    onToken?: (token: { node_id: string; token: string; done: boolean }) => void,
    onError?: (err: { kind: "network" | "parse" | "http"; message: string }) => void,
  ): () => void {
    // Auth/tenant goes in headers — not the URL — so it does not leak to
    // proxy or CDN access logs. `openSSE` uses fetch + ReadableStream under
    // the hood, unlike EventSource which cannot send custom headers.
    const url = `${API_BASE}/api/v1/workflows/${workflowId}/instances/${instanceId}/stream`;
    let receivedDone = false;

    const safeParse = <T>(raw: string, eventName: string): T | null => {
      try {
        return JSON.parse(raw) as T;
      } catch (e) {
        onError?.({ kind: "parse", message: `${eventName}: ${String(e)}` });
        return null;
      }
    };

    return openSSE(url, getAuthHeaders(), {
      onEvent: (event, data) => {
        switch (event) {
          case "log": {
            const parsed = safeParse<Partial<ExecutionLogOut>>(data, "log");
            if (parsed) onLog(parsed);
            break;
          }
          case "status": {
            const parsed = safeParse<{ instance_status: string; current_node_id?: string | null }>(
              data,
              "status",
            );
            if (parsed) onStatus(parsed);
            break;
          }
          case "token": {
            if (!onToken) break;
            const parsed = safeParse<{ node_id: string; token: string; done: boolean }>(data, "token");
            if (parsed) onToken(parsed);
            break;
          }
          case "done": {
            receivedDone = true;
            break;
          }
        }
      },
      onError: (err) => {
        if (!receivedDone) onError?.(err);
      },
      onDone: () => {
        onDone();
      },
    });
  },

  // ---------------------------------------------------------------------------
  // Knowledge Bases
  // ---------------------------------------------------------------------------

  listKnowledgeBases(): Promise<KBOut[]> {
    return request("/api/v1/knowledge-bases");
  },

  createKnowledgeBase(body: {
    name: string;
    description?: string;
    embedding_provider?: string;
    embedding_model?: string;
    vector_store?: string;
    chunking_strategy?: string;
    chunk_size?: number;
    chunk_overlap?: number;
    semantic_threshold?: number | null;
  }): Promise<KBOut> {
    return request("/api/v1/knowledge-bases", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getKnowledgeBase(id: string): Promise<KBOut> {
    return request(`/api/v1/knowledge-bases/${id}`);
  },

  updateKnowledgeBase(id: string, body: { name?: string; description?: string }): Promise<KBOut> {
    return request(`/api/v1/knowledge-bases/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  },

  deleteKnowledgeBase(id: string): Promise<void> {
    return request(`/api/v1/knowledge-bases/${id}`, { method: "DELETE" });
  },

  // Documents
  async uploadDocument(kbId: string, file: File): Promise<KBDocumentOut> {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/api/v1/knowledge-bases/${kbId}/documents`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: formData,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new ApiError(`API ${res.status}: ${body}`, res.status, body);
    }
    return res.json() as Promise<KBDocumentOut>;
  },

  listDocuments(kbId: string): Promise<KBDocumentOut[]> {
    return request(`/api/v1/knowledge-bases/${kbId}/documents`);
  },

  deleteDocument(kbId: string, docId: string): Promise<void> {
    return request(`/api/v1/knowledge-bases/${kbId}/documents/${docId}`, { method: "DELETE" });
  },

  searchKnowledgeBase(kbId: string, query: string, topK: number = 5): Promise<KBChunkOut[]> {
    return request(`/api/v1/knowledge-bases/${kbId}/search`, {
      method: "POST",
      body: JSON.stringify({ query, top_k: topK }),
    });
  },

  // Embedding / chunking options (drives KB creation form)
  getEmbeddingOptions(): Promise<EmbeddingOption[]> {
    return request("/api/v1/knowledge-bases/embedding-options");
  },

  getChunkingStrategies(): Promise<ChunkingStrategy[]> {
    return request("/api/v1/knowledge-bases/chunking-strategies");
  },

  getVectorStores(): Promise<VectorStoreOption[]> {
    return request("/api/v1/knowledge-bases/vector-stores");
  },

  // ---------------------------------------------------------------------------
  // Tenant Secrets (Credential Vault)
  // ---------------------------------------------------------------------------

  listSecrets(): Promise<SecretOut[]> {
    return request("/api/v1/secrets");
  },

  createSecret(key_name: string, value: string): Promise<SecretOut> {
    return request("/api/v1/secrets", {
      method: "POST",
      body: JSON.stringify({ key_name, value }),
    });
  },

  updateSecret(id: string, value: string): Promise<SecretOut> {
    return request(`/api/v1/secrets/${id}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },

  deleteSecret(id: string): Promise<void> {
    return request(`/api/v1/secrets/${id}`, { method: "DELETE" });
  },

  // ---------------------------------------------------------------------------
  // Tenant Integrations (external-system connection defaults)
  // ---------------------------------------------------------------------------

  listIntegrations(system?: string): Promise<TenantIntegrationOut[]> {
    const qs = system ? `?system=${encodeURIComponent(system)}` : "";
    return request(`/api/v1/tenant-integrations${qs}`);
  },

  createIntegration(body: TenantIntegrationCreate): Promise<TenantIntegrationOut> {
    return request("/api/v1/tenant-integrations", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  updateIntegration(
    id: string,
    body: TenantIntegrationUpdate,
  ): Promise<TenantIntegrationOut> {
    return request(`/api/v1/tenant-integrations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deleteIntegration(id: string): Promise<void> {
    return request(`/api/v1/tenant-integrations/${id}`, { method: "DELETE" });
  },

  // ---------------------------------------------------------------------------
  // MCP-02 — per-tenant MCP server registry
  // ---------------------------------------------------------------------------

  listMcpServers(): Promise<TenantMcpServerOut[]> {
    return request("/api/v1/tenant-mcp-servers");
  },

  createMcpServer(body: TenantMcpServerCreate): Promise<TenantMcpServerOut> {
    return request("/api/v1/tenant-mcp-servers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  updateMcpServer(
    id: string,
    body: TenantMcpServerUpdate,
  ): Promise<TenantMcpServerOut> {
    return request(`/api/v1/tenant-mcp-servers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deleteMcpServer(id: string): Promise<void> {
    return request(`/api/v1/tenant-mcp-servers/${id}`, { method: "DELETE" });
  },

  // ---------------------------------------------------------------------------
  // ADMIN-01 — per-tenant policy singleton
  // ---------------------------------------------------------------------------

  getTenantPolicy(): Promise<TenantPolicyOut> {
    return request("/api/v1/tenant-policy");
  },

  updateTenantPolicy(body: TenantPolicyUpdate): Promise<TenantPolicyOut> {
    return request("/api/v1/tenant-policy", {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  // ---------------------------------------------------------------------------
  // STARTUP-01 — readiness probe
  // ---------------------------------------------------------------------------

  // ADMIN-03 — read-only LLM credentials status.
  getLlmCredentials(): Promise<LlmCredentialsOut> {
    return request("/api/v1/llm-credentials");
  },

  async getHealthReady(): Promise<HealthReadyOut> {
    // /health/ready returns 503 when checks fail but the body is
    // still structured JSON — callers want to render per-check
    // remediation in both states. Bypass the request() helper which
    // would throw on non-2xx.
    const res = await fetch(`${API_BASE}/health/ready`, {
      headers: { ...getAuthHeaders() },
    });
    return (await res.json()) as HealthReadyOut;
  },

  // ---------------------------------------------------------------------------
  // COPILOT-01 — workflow authoring copilot (draft workspace + tool layer)
  //
  // The UI surface lands in COPILOT-02; these typed bindings exist now so
  // the chat pane can be a pure frontend change against a stable contract.
  // ---------------------------------------------------------------------------

  listDrafts(): Promise<CopilotDraftOut[]> {
    return request("/api/v1/copilot/drafts");
  },

  createDraft(body: {
    title: string;
    base_workflow_id?: string;
  }): Promise<CopilotDraftOut> {
    return request("/api/v1/copilot/drafts", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getDraft(draftId: string): Promise<CopilotDraftOut> {
    return request(`/api/v1/copilot/drafts/${draftId}`);
  },

  updateDraft(
    draftId: string,
    body: {
      title?: string;
      graph_json?: { nodes: unknown[]; edges: unknown[] };
      expected_version?: number;
    },
  ): Promise<CopilotDraftOut> {
    return request(`/api/v1/copilot/drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deleteDraft(draftId: string): Promise<void> {
    return request(`/api/v1/copilot/drafts/${draftId}`, { method: "DELETE" });
  },

  callCopilotTool(
    draftId: string,
    toolName: CopilotToolName,
    args: Record<string, unknown>,
    expectedVersion?: number,
  ): Promise<CopilotToolCallOut> {
    return request(`/api/v1/copilot/drafts/${draftId}/tools/${toolName}`, {
      method: "POST",
      body: JSON.stringify({
        args,
        expected_version: expectedVersion,
      }),
    });
  },

  promoteDraft(
    draftId: string,
    body: {
      name?: string;
      description?: string;
      expected_version?: number;
    },
  ): Promise<CopilotPromoteOut> {
    return request(`/api/v1/copilot/drafts/${draftId}/promote`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // --- COPILOT-03.e — scenario list + run-all for PromoteDialog ---

  listDraftScenarios(draftId: string): Promise<CopilotScenarioOut[]> {
    return request(`/api/v1/copilot/drafts/${draftId}/scenarios`);
  },

  runAllDraftScenarios(draftId: string): Promise<CopilotScenariosRunAllOut> {
    return request(`/api/v1/copilot/drafts/${draftId}/scenarios/run_all`, {
      method: "POST",
    });
  },

  // --- COPILOT-01b — sessions + turn streaming ---

  getCopilotProviders(): Promise<CopilotProvidersOut> {
    return request("/api/v1/copilot/sessions/providers");
  },

  listCopilotSessions(draftId?: string): Promise<CopilotSessionOut[]> {
    const qs = draftId ? `?draft_id=${encodeURIComponent(draftId)}` : "";
    return request(`/api/v1/copilot/sessions${qs}`);
  },

  createCopilotSession(body: {
    draft_id: string;
    provider?: string;
    model?: string;
  }): Promise<CopilotSessionOut> {
    return request("/api/v1/copilot/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getCopilotSession(sessionId: string): Promise<CopilotSessionOut> {
    return request(`/api/v1/copilot/sessions/${sessionId}`);
  },

  abandonCopilotSession(sessionId: string): Promise<void> {
    return request(`/api/v1/copilot/sessions/${sessionId}`, { method: "DELETE" });
  },

  listCopilotTurns(sessionId: string): Promise<CopilotTurnOut[]> {
    return request(`/api/v1/copilot/sessions/${sessionId}/turns`);
  },

  /**
   * Send a user message and stream the agent's response as
   * {@link CopilotAgentEvent} items. Yields each event as it arrives.
   *
   * EventSource isn't used because EventSource can't POST a body.
   * We open a streaming fetch, parse `data: ...\n\n` frames by hand.
   */
  async *sendCopilotTurn(
    sessionId: string,
    text: string,
    signal?: AbortSignal,
  ): AsyncGenerator<CopilotAgentEvent> {
    const res = await fetch(
      `${API_BASE}/api/v1/copilot/sessions/${sessionId}/turns`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ text }),
        signal,
      },
    );
    if (!res.ok) {
      const bodyText = await res.text().catch(() => "");
      throw new Error(
        `Copilot turn failed: HTTP ${res.status} ${res.statusText} ${bodyText}`,
      );
    }
    if (!res.body) {
      throw new Error("Copilot turn response has no body (streaming required)");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frame boundary is a blank line.
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const payload = frame
            .split("\n")
            .filter((l) => l.startsWith("data: "))
            .map((l) => l.slice(6))
            .join("\n");
          if (!payload) continue;
          try {
            yield JSON.parse(payload) as CopilotAgentEvent;
          } catch (err) {
            // A malformed frame shouldn't kill the stream — emit an
            // error event the chat pane can surface and keep reading.
            yield {
              type: "error",
              message: `Could not parse agent event: ${
                err instanceof Error ? err.message : String(err)
              }`,
              recoverable: true,
            };
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  },
};
