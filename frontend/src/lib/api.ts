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

export interface InstanceDetailOut extends InstanceOut {
  logs: ExecutionLogOut[];
}

export interface ToolOut {
  name: string;
  title: string;
  description: string;
  category: string;
  safety_tier: string;
  tags: string[];
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
};
