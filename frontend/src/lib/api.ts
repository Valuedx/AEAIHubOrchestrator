const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8001";
const TENANT_ID = import.meta.env.VITE_TENANT_ID || "default";

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
  const token = localStorage.getItem("ae_access_token");
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

// Tenant Secrets
export interface SecretOut {
  id: string;
  key_name: string;
  created_at: string;
  updated_at: string;
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
  ): Promise<InstanceOut> {
    return request(`/api/v1/workflows/${workflowId}/instances/${instanceId}/callback`, {
      method: "POST",
      body: JSON.stringify({
        approval_payload: approvalPayload,
        context_patch: contextPatch ?? null,
      }),
    });
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

  getInstanceDetail(
    workflowId: string,
    instanceId: string,
  ): Promise<InstanceDetailOut> {
    return request(
      `/api/v1/workflows/${workflowId}/instances/${instanceId}`,
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
  ): () => void {
    const url = `${API_BASE}/api/v1/workflows/${workflowId}/instances/${instanceId}/stream?x_tenant_id=${TENANT_ID}`;
    const es = new EventSource(url);

    es.addEventListener("log", (e) => {
      onLog(JSON.parse(e.data));
    });
    es.addEventListener("status", (e) => {
      onStatus(JSON.parse(e.data));
    });
    es.addEventListener("token", (e) => {
      if (onToken) onToken(JSON.parse(e.data));
    });
    es.addEventListener("done", () => {
      onDone();
      es.close();
    });
    es.onerror = () => {
      es.close();
      onDone();
    };

    return () => es.close();
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
};
