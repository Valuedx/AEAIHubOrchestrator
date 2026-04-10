import { create } from "zustand";
import type { Edge, Node } from "@xyflow/react";
import {
  api,
  ApiError,
  type WorkflowOut,
  type InstanceOut,
  type InstanceDetailOut,
  type InstanceContextOut,
  type SyncExecuteOut,
  type CheckpointOut,
  type CheckpointDetailOut,
} from "@/lib/api";
import { useFlowStore } from "@/store/flowStore";
import { getWorkflowTemplate } from "@/lib/templates";
import type { AgenticNodeData } from "@/types/nodes";

interface WorkflowState {
  currentWorkflow: WorkflowOut | null;
  workflows: WorkflowOut[];
  instances: InstanceOut[];
  isDirty: boolean;
  /** Definition version currently rendered on the canvas, if it maps to a saved workflow. */
  canvasDefinitionVersion: number | null;

  activeInstance: InstanceDetailOut | null;
  isExecuting: boolean;

  /** Context snapshot loaded for HITL review of a suspended instance. */
  instanceContext: InstanceContextOut | null;

  /**
   * Live streaming token buffer per node_id.
   * Cleared when execution starts; accumulated as ``token`` SSE events arrive.
   * When a node's ``done: true`` message arrives the buffer is preserved
   * (the final LLM response will overwrite it via the log event shortly after).
   */
  streamingTokens: Record<string, string>;

  loading: boolean;
  error: string | null;
  /** Non-error banner (e.g. historical graph restored for replay). */
  notice: string | null;

  _sseCleanup: (() => void) | null;
  dismissError: () => void;
  dismissNotice: () => void;

  fetchWorkflows: () => Promise<void>;
  fetchInstances: (workflowId: string) => Promise<void>;
  loadWorkflow: (id: string) => Promise<void>;
  saveWorkflow: (name?: string) => Promise<void>;
  deleteWorkflow: (id: string) => Promise<void>;
  newWorkflow: () => void;
  /** Load a bundled template by id (see `lib/templates`). */
  loadTemplate: (templateId: string) => void;
  /** Replace canvas from portable `{ nodes, edges }` JSON. */
  importGraphJson: (graph: { nodes: unknown[]; edges: unknown[] }) => void;
  /** Downloadable JSON blob of the current canvas. */
  exportCurrentGraph: () => Blob;
  markDirty: () => void;

  /** When true, Run uses synchronous execute (API holds until terminal status). */
  runSync: boolean;
  setRunSync: (v: boolean) => void;

  executeWorkflow: (triggerPayload?: Record<string, unknown>) => Promise<void>;
  /** Ask backend to cancel after the current node finishes (between nodes). */
  cancelInstance: (workflowId: string, instanceId: string) => Promise<void>;
  /** Ask backend to pause after the current node finishes (between nodes). */
  pauseInstance: (workflowId: string, instanceId: string) => Promise<void>;
  /** Resume an instance paused between nodes. */
  resumePausedInstance: (
    workflowId: string,
    instanceId: string,
    contextPatch?: Record<string, unknown>,
  ) => Promise<void>;
  retryInstance: (workflowId: string, instanceId: string, fromNodeId?: string) => Promise<void>;
  /** Fetch and cache the context snapshot for a suspended instance. */
  fetchInstanceContext: (workflowId: string, instanceId: string) => Promise<void>;
  /** Resume a suspended instance with optional approval payload and context patch. */
  resumeInstance: (
    workflowId: string,
    instanceId: string,
    approvalPayload: Record<string, unknown>,
    contextPatch?: Record<string, unknown>,
  ) => Promise<void>;
  pollInstance: (workflowId: string, instanceId: string) => Promise<void>;
  streamInstance: (workflowId: string, instanceId: string) => void;
  clearExecution: () => void;
  /** Load instance from Execution history: detail, optional canvas align, SSE. */
  openInstanceFromHistory: (workflowId: string, instanceId: string) => Promise<void>;
  alignCanvasToInstanceVersion: (
    workflowId: string,
    instance: InstanceDetailOut,
  ) => Promise<void>;

  /** Step-through replay using server checkpoints (terminal runs). */
  isDebugMode: boolean;
  debugCheckpoints: CheckpointOut[];
  activeCheckpointIdx: number | null;
  activeCheckpointDetail: CheckpointDetailOut | null;
  /** Separate loading flag scoped to debug replay (avoids clashing with save/load). */
  debugLoading: boolean;
  enterDebugMode: () => Promise<void>;
  exitDebugMode: () => void;
  selectCheckpointIdx: (idx: number) => Promise<void>;
  stepDebugPrev: () => Promise<void>;
  stepDebugNext: () => Promise<void>;
}

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  currentWorkflow: null,
  workflows: [],
  instances: [],
  isDirty: false,
  canvasDefinitionVersion: null,
  activeInstance: null,
  isExecuting: false,
  instanceContext: null,
  streamingTokens: {},
  loading: false,
  error: null,
  notice: null,
  _sseCleanup: null,

  dismissError: () => set({ error: null }),
  dismissNotice: () => set({ notice: null }),
  runSync: false,
  isDebugMode: false,
  debugCheckpoints: [],
  activeCheckpointIdx: null,
  activeCheckpointDetail: null,
  debugLoading: false,

  setRunSync: (v) => set({ runSync: v }),

  enterDebugMode: async () => {
    const wf = get().currentWorkflow;
    const inst = get().activeInstance;
    if (!wf || !inst) return;
    set({ debugLoading: true, error: null });
    try {
      const cps = await api.listCheckpoints(wf.id, inst.id);
      set({
        debugCheckpoints: cps,
        isDebugMode: true,
        activeCheckpointIdx: null,
        activeCheckpointDetail: null,
        debugLoading: false,
      });
      if (cps.length > 0) {
        await get().selectCheckpointIdx(0);
      }
    } catch (e) {
      set({
        error: String(e),
        debugLoading: false,
        isDebugMode: false,
        debugCheckpoints: [],
      });
    }
  },

  exitDebugMode: () => {
    const fs = useFlowStore.getState();
    for (const n of fs.nodes) {
      fs.updateNodeData(n.id, { status: "idle" });
    }
    set({
      isDebugMode: false,
      debugCheckpoints: [],
      activeCheckpointIdx: null,
      activeCheckpointDetail: null,
      debugLoading: false,
    });
  },

  selectCheckpointIdx: async (idx) => {
    const wf = get().currentWorkflow;
    const inst = get().activeInstance;
    const cps = get().debugCheckpoints;
    if (!wf || !inst || idx < 0 || idx >= cps.length) return;
    set({ debugLoading: true, error: null });
    try {
      const detail = await api.getCheckpointDetail(wf.id, inst.id, cps[idx].id);
      const completed = new Set<string>();
      for (let j = 0; j < idx; j++) {
        completed.add(cps[j].node_id);
      }
      const cursor = cps[idx].node_id;
      const fs = useFlowStore.getState();
      for (const n of fs.nodes) {
        let st: NonNullable<AgenticNodeData["status"]> = "idle";
        if (n.id === cursor) st = "running";
        else if (completed.has(n.id)) st = "completed";
        fs.updateNodeData(n.id, { status: st });
      }
      set({
        activeCheckpointIdx: idx,
        activeCheckpointDetail: detail,
        debugLoading: false,
      });
    } catch (e) {
      set({ error: String(e), debugLoading: false });
    }
  },

  stepDebugPrev: async () => {
    const i = get().activeCheckpointIdx;
    const cps = get().debugCheckpoints;
    if (i == null || i <= 0 || cps.length === 0) return;
    await get().selectCheckpointIdx(i - 1);
  },

  stepDebugNext: async () => {
    const i = get().activeCheckpointIdx;
    const cps = get().debugCheckpoints;
    if (i == null || i >= cps.length - 1) return;
    await get().selectCheckpointIdx(i + 1);
  },

  fetchWorkflows: async () => {
    set({ loading: true, error: null });
    try {
      const workflows = await api.listWorkflows();
      set({ workflows, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  fetchInstances: async (workflowId) => {
    set({ loading: true, error: null });
    try {
      const instances = await api.listInstances(workflowId);
      // Sort by newest first
      instances.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      set({ instances, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  loadWorkflow: async (id) => {
    set({ loading: true, error: null });
    try {
      const wf = await api.getWorkflow(id);
      const graph = wf.graph_json;
      const newNodes = (graph.nodes ?? []) as Node[];
      const newEdges = (graph.edges ?? []) as Edge[];

      useFlowStore.getState().replaceGraph(newNodes, newEdges);

      set({
        currentWorkflow: wf,
        isDirty: false,
        canvasDefinitionVersion: wf.version,
        loading: false,
        activeInstance: null,
        isDebugMode: false,
        debugCheckpoints: [],
        activeCheckpointIdx: null,
        activeCheckpointDetail: null,
        debugLoading: false,
        notice: null,
      });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  saveWorkflow: async (name) => {
    set({ loading: true, error: null });
    try {
      const { nodes, edges } = useFlowStore.getState();
      const graph_json = { nodes, edges };
      const current = get().currentWorkflow;

      let wf: WorkflowOut;
      if (current) {
        wf = await api.updateWorkflow(current.id, {
          name: name ?? current.name,
          graph_json,
        });
      } else {
        wf = await api.createWorkflow({
          name: name || "Untitled Workflow",
          graph_json,
        });
      }

      set({
        currentWorkflow: wf,
        isDirty: false,
        canvasDefinitionVersion: wf.version,
        loading: false,
        notice: null,
      });
      get().fetchWorkflows();
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  deleteWorkflow: async (id) => {
    set({ loading: true, error: null });
    try {
      await api.deleteWorkflow(id);
      const current = get().currentWorkflow;
      if (current?.id === id) {
        get().newWorkflow();
      }
      set({ loading: false });
      get().fetchWorkflows();
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  newWorkflow: () => {
    useFlowStore.getState().replaceGraph([], []);
    set({
      currentWorkflow: null,
      isDirty: false,
      canvasDefinitionVersion: null,
      activeInstance: null,
      isDebugMode: false,
      debugCheckpoints: [],
      activeCheckpointIdx: null,
      activeCheckpointDetail: null,
      debugLoading: false,
      notice: null,
      error: null,
    });
  },

  loadTemplate: (templateId) => {
    const t = getWorkflowTemplate(templateId);
    if (!t) {
      set({ error: `Unknown template: ${templateId}` });
      return;
    }
    const prev = get()._sseCleanup;
    if (prev) prev();
    const { nodes, edges } = t.graph;
    useFlowStore.getState().replaceGraph(nodes, edges);
    set({
      currentWorkflow: null,
      isDirty: true,
      canvasDefinitionVersion: null,
      activeInstance: null,
      isExecuting: false,
      _sseCleanup: null,
      error: null,
      notice: null,
      isDebugMode: false,
      debugCheckpoints: [],
      activeCheckpointIdx: null,
      activeCheckpointDetail: null,
      debugLoading: false,
    });
  },

  importGraphJson: (graph) => {
    const prev = get()._sseCleanup;
    if (prev) prev();
    const newNodes = (graph.nodes ?? []) as Node[];
    const newEdges = (graph.edges ?? []) as Edge[];
    useFlowStore.getState().replaceGraph(newNodes, newEdges);
    set({
      currentWorkflow: null,
      isDirty: true,
      canvasDefinitionVersion: null,
      activeInstance: null,
      isExecuting: false,
      _sseCleanup: null,
      error: null,
      notice: null,
      isDebugMode: false,
      debugCheckpoints: [],
      activeCheckpointIdx: null,
      activeCheckpointDetail: null,
      debugLoading: false,
    });
  },

  exportCurrentGraph: () => {
    const { nodes, edges } = useFlowStore.getState();
    const graph_json = { nodes, edges };
    return new Blob([JSON.stringify(graph_json, null, 2)], {
      type: "application/json",
    });
  },

  markDirty: () => {
    set({ isDirty: true });
  },

  executeWorkflow: async (triggerPayload) => {
    const wf = get().currentWorkflow;
    if (!wf) {
      set({
        error:
          "Save the workflow before running. Use Save in the toolbar after loading a template or importing JSON.",
      });
      return;
    }

    set({ isExecuting: true, error: null, notice: null, streamingTokens: {} });
    try {
      if (get().isDirty) {
        await get().saveWorkflow();
      }
      const wfNow = get().currentWorkflow ?? wf;
      const result = await api.executeWorkflow(
        wfNow.id,
        triggerPayload,
        undefined,
        get().runSync,
      );
      const isSync = (r: InstanceOut | SyncExecuteOut): r is SyncExecuteOut =>
        "instance_id" in r && "output" in r && !("id" in r);
      if (isSync(result)) {
        let detail: InstanceDetailOut;
        try {
          detail = await api.getInstanceDetail(wfNow.id, result.instance_id);
        } catch {
          detail = {
            id: result.instance_id,
            tenant_id: "",
            workflow_def_id: wfNow.id,
            status: result.status,
            current_node_id: null,
            started_at: result.started_at,
            completed_at: result.completed_at,
            created_at: result.started_at ?? new Date().toISOString(),
            logs: [],
            definition_version_at_start: wfNow.version,
          };
        }
        set({
          activeInstance: detail,
          isExecuting: false,
        });
      } else {
        const instance = result as InstanceOut;
        set({
          activeInstance: { ...instance, logs: [] },
          isExecuting: true,
        });
        get().streamInstance(wfNow.id, instance.id);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 504) {
        const rawId = e.json?.instance_id;
        const iid = typeof rawId === "string" ? rawId : null;
        const wfNow = get().currentWorkflow;
        if (iid && wfNow) {
          set({
            error:
              "Synchronous run timed out; the workflow may still be running. Subscribing to live updates below.",
            isExecuting: true,
            activeInstance: {
              id: iid,
              tenant_id: "",
              workflow_def_id: wfNow.id,
              status: "running",
              current_node_id: null,
              started_at: null,
              completed_at: null,
              created_at: new Date().toISOString(),
              logs: [],
              definition_version_at_start: wfNow.version,
            },
          });
          get().streamInstance(wfNow.id, iid);
          void api
            .getInstanceDetail(wfNow.id, iid)
            .then((detail) => {
              set({ activeInstance: detail });
            })
            .catch(() => {});
          return;
        }
        if (iid) {
          set({
            error: `Synchronous run timed out. Instance id: ${iid}. Open this workflow and use Execution history, or poll GET …/instances/${iid}.`,
            isExecuting: false,
            activeInstance: null,
          });
          return;
        }
      }
      set({ error: String(e), isExecuting: false, activeInstance: null });
    }
  },

  cancelInstance: async (workflowId, instanceId) => {
    try {
      const updated = await api.cancelInstance(workflowId, instanceId);
      const inst = get().activeInstance;
      if (inst && inst.id === instanceId) {
        set({ activeInstance: { ...inst, status: updated.status } });
      }
    } catch (e) {
      set({ error: String(e) });
    }
  },

  pauseInstance: async (workflowId, instanceId) => {
    try {
      const updated = await api.pauseInstance(workflowId, instanceId);
      const inst = get().activeInstance;
      if (inst && inst.id === instanceId) {
        set({ activeInstance: { ...inst, status: updated.status } });
      }
    } catch (e) {
      set({ error: String(e) });
    }
  },

  resumePausedInstance: async (workflowId, instanceId, contextPatch) => {
    set({ isExecuting: true, error: null });
    try {
      const instance = await api.resumePausedInstance(workflowId, instanceId, contextPatch);
      set({
        activeInstance: { ...instance, logs: get().activeInstance?.logs ?? [] },
        isExecuting: true,
      });
      get().streamInstance(workflowId, instance.id);
    } catch (e) {
      set({ error: String(e), isExecuting: false });
    }
  },

  retryInstance: async (workflowId, instanceId, fromNodeId) => {
    set({ isExecuting: true, error: null });
    try {
      const instance = await api.retryInstance(workflowId, instanceId, fromNodeId);
      set({
        activeInstance: { ...instance, logs: [] },
        isExecuting: true,
      });
      get().streamInstance(workflowId, instance.id);
    } catch (e) {
      set({ error: String(e), isExecuting: false });
    }
  },

  streamInstance: (workflowId, instanceId) => {
    const prev = get()._sseCleanup;
    if (prev) prev();

    const cleanup = api.streamInstance(
      workflowId,
      instanceId,
      (log) => {
        const inst = get().activeInstance;
        if (!inst) return;
        const existing = inst.logs.find((l) => l.id === log.id);
        const logs = existing
          ? inst.logs.map((l) => (l.id === log.id ? { ...l, ...log } : l))
          : [...inst.logs, log as InstanceDetailOut["logs"][number]];
        set({ activeInstance: { ...inst, logs } });
      },
      (status) => {
        const inst = get().activeInstance;
        if (!inst) return;
        set({ activeInstance: { ...inst, status: status.instance_status, current_node_id: status.current_node_id ?? inst.current_node_id } });
      },
      () => {
        set({ isExecuting: false, _sseCleanup: null, streamingTokens: {} });
        const wf = get().currentWorkflow;
        const inst = get().activeInstance;
        if (wf && inst) {
          api.getInstanceDetail(wf.id, inst.id).then((detail) => {
            set({ activeInstance: detail });
          }).catch(() => {});
        }
      },
      (tokenEvent) => {
        if (tokenEvent.done) return; // keep buffer; log event will overwrite shortly
        set((state) => ({
          streamingTokens: {
            ...state.streamingTokens,
            [tokenEvent.node_id]: (state.streamingTokens[tokenEvent.node_id] ?? "") + tokenEvent.token,
          },
        }));
      },
    );

    set({ _sseCleanup: cleanup });
  },

  fetchInstanceContext: async (workflowId, instanceId) => {
    try {
      const ctx = await api.getInstanceContext(workflowId, instanceId);
      set({ instanceContext: ctx });
    } catch (e) {
      set({ error: String(e) });
    }
  },

  resumeInstance: async (workflowId, instanceId, approvalPayload, contextPatch) => {
    set({ isExecuting: true, error: null, instanceContext: null });
    try {
      const instance = await api.callbackWorkflow(workflowId, instanceId, approvalPayload, contextPatch);
      set({
        activeInstance: { ...instance, logs: get().activeInstance?.logs ?? [] },
        isExecuting: true,
      });
      get().streamInstance(workflowId, instance.id);
    } catch (e) {
      set({ error: String(e), isExecuting: false });
    }
  },

  alignCanvasToInstanceVersion: async (workflowId, instance) => {
    const v = instance.definition_version_at_start;
    const wf = get().currentWorkflow;
    const canvasVersion = get().canvasDefinitionVersion;
    if (v == null || !wf || wf.id !== workflowId) return;
    if (canvasVersion === v) return;
    if (v === wf.version) {
      const nodes = (wf.graph_json.nodes ?? []) as Node[];
      const edges = (wf.graph_json.edges ?? []) as Edge[];
      useFlowStore.getState().replaceGraph(nodes, edges);
      set({
        isDirty: false,
        canvasDefinitionVersion: wf.version,
      });
      return;
    }
    try {
      const { graph_json } = await api.getGraphAtVersion(workflowId, v);
      const nodes = (graph_json.nodes ?? []) as Node[];
      const edges = (graph_json.edges ?? []) as Edge[];
      useFlowStore.getState().replaceGraph(nodes, edges);
      set({
        isDirty: true,
        canvasDefinitionVersion: v,
        notice: `Canvas restored to definition version ${v} from when this run started. The saved workflow is still version ${wf.version}. Save to keep this graph as a new revision, or reload the workflow to return to the latest version.`,
      });
    } catch {
      set({
        notice: `Could not load the historical graph for version ${v}. Replay overlays may not match the canvas.`,
      });
    }
  },

  openInstanceFromHistory: async (workflowId, instanceId) => {
    set({ error: null, notice: null });
    try {
      const detail = await api.getInstanceDetail(workflowId, instanceId);
      await get().alignCanvasToInstanceVersion(workflowId, detail);
      const running = detail.status === "queued" || detail.status === "running";
      const prev = get()._sseCleanup;
      if (prev) prev();
      set({
        activeInstance: detail,
        isExecuting: running,
        _sseCleanup: null,
        streamingTokens: {},
        isDebugMode: false,
        debugCheckpoints: [],
        activeCheckpointIdx: null,
        activeCheckpointDetail: null,
        debugLoading: false,
      });
      if (running) {
        get().streamInstance(workflowId, instanceId);
      }
    } catch (e) {
      set({ error: String(e) });
    }
  },

  pollInstance: async (workflowId, instanceId) => {
    const poll = async () => {
      try {
        const detail = await api.getInstanceDetail(workflowId, instanceId);
        set({ activeInstance: detail });

        if (
          ["completed", "failed", "suspended", "cancelled", "paused"].includes(
            detail.status,
          )
        ) {
          set({ isExecuting: false });
          return;
        }
        setTimeout(poll, 1500);
      } catch {
        set({ isExecuting: false });
      }
    };
    poll();
  },

  clearExecution: () => {
    const prev = get()._sseCleanup;
    if (prev) prev();
    if (get().isDebugMode) {
      const fs = useFlowStore.getState();
      for (const n of fs.nodes) {
        fs.updateNodeData(n.id, { status: "idle" });
      }
    }
    set({
      activeInstance: null,
      isExecuting: false,
      _sseCleanup: null,
      isDebugMode: false,
      debugCheckpoints: [],
      activeCheckpointIdx: null,
      activeCheckpointDetail: null,
      debugLoading: false,
      notice: null,
    });
  },
}));
