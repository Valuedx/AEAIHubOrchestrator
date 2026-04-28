import { create } from "zustand";
import {
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type XYPosition,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
} from "@xyflow/react";
import type { AgenticNodeData, NodeCategory } from "@/types/nodes";
import {
  STICKY_NOTE_DEFAULT_DATA,
  type StickyNoteData,
} from "@/types/stickyNote";
import {
  LOOPBACK_DEFAULT_MAX_ITERATIONS,
  hydrateEdgesFromLoad,
} from "@/types/edges";

let nodeIdCounter = 0;
const nextId = () => `node_${++nodeIdCounter}`;

function syncNodeIdCounterFromNodes(nodes: Node[]) {
  let max = 0;
  for (const n of nodes) {
    const m = /^node_(\d+)$/.exec(n.id);
    if (m) max = Math.max(max, Number.parseInt(m[1], 10));
  }
  nodeIdCounter = max;
}

const MAX_HISTORY = 50;

type Snapshot = { nodes: Node[]; edges: Edge[] };

/**
 * COPILOT-02.ii.b — annotation placed on each node of the preview
 * graph so AgenticNode can render added-vs-modified styling. The
 * diff is computed once in ``setCopilotPreview`` instead of in each
 * node render, so repaints stay cheap.
 */
export type CopilotDiffStatus = "added" | "modified" | "unchanged";

export interface CopilotPreviewGraph {
  nodes: Node[];
  edges: Edge[];
  addedNodeIds: string[];
  modifiedNodeIds: string[];
  addedEdgeIds: string[];
}

interface FlowState {
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  /** CYCLIC-01.d — the edge currently open in the EdgeInspector.
   *  Mutually exclusive with ``selectedNodeId`` so only one panel
   *  is visible at a time. ``null`` hides the edge inspector. */
  selectedEdgeId: string | null;

  /** Undo/redo history stacks */
  past: Snapshot[];
  future: Snapshot[];
  /** Tracks node IDs currently being dragged so we snapshot once per drag */
  _draggingNodeIds: Set<string>;

  /**
   * COPILOT-02.ii.b — when set, the canvas renders this snapshot
   * read-only instead of the editable ``nodes`` / ``edges``. Each
   * node carries a ``data.__copilotDiff`` annotation (`added` /
   * `modified` / `unchanged`) so the node component can style
   * itself. Clearing returns the canvas to the base workflow view;
   * editing is disabled while set.
   */
  copilotPreview: CopilotPreviewGraph | null;

  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;

  /** Undo the last canvas action */
  undo: () => void;
  /** Redo the last undone action */
  redo: () => void;
  /** Push current state onto the undo stack (clears redo stack) */
  _pushHistory: () => void;

  /** Replace canvas state and align `node_*` id counter for new nodes from the palette. */
  replaceGraph: (nodes: Node[], edges: Edge[]) => void;

  addNode: (
    nodeCategory: NodeCategory,
    label: string,
    position: XYPosition,
    defaultConfig?: Record<string, unknown>,
  ) => void;
  selectNode: (id: string | null) => void;
  /** CYCLIC-01.d — select an edge (mutually exclusive with node
   *  selection). Passing ``null`` closes the EdgeInspector. */
  selectEdge: (id: string | null) => void;
  /** CYCLIC-01.d — merge a partial patch into an edge.
   *  EdgeInspector uses this to write ``maxIterations`` /
   *  ``type: "loopback"`` etc. Pushes history so undo works. */
  updateEdge: (id: string, patch: Partial<Edge>) => void;
  updateNodeData: (
    id: string,
    data: Partial<AgenticNodeData> | Partial<StickyNoteData>,
  ) => void;
  deleteNode: (id: string) => void;

  /** DV-03 — add a sticky note at the given canvas position. Returns
   *  the new node's id so callers can scroll / select it. */
  addStickyNote: (position: XYPosition) => string;

  /**
   * COPILOT-02.ii.b — pass the draft's graph (and the base workflow's
   * graph, if any) to switch the canvas into read-only preview mode.
   * The diff between draft and base is computed here once; passing
   * ``null`` for ``baseGraph`` treats everything as added (net-new
   * drafts).
   */
  setCopilotPreview: (
    draftGraph: { nodes: unknown[]; edges: unknown[] } | null,
    baseGraph: { nodes: unknown[]; edges: unknown[] } | null,
  ) => void;
  /** Exit preview mode — canvas returns to showing the live nodes/edges. */
  clearCopilotPreview: () => void;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  selectedEdgeId: null,
  past: [],
  future: [],
  _draggingNodeIds: new Set(),
  copilotPreview: null,

  _pushHistory: () => {
    const { nodes, edges, past } = get();
    set({
      past: [...past.slice(-(MAX_HISTORY - 1)), { nodes: [...nodes], edges: [...edges] }],
      future: [],
    });
  },

  undo: () => {
    const { past, nodes, edges, future } = get();
    if (past.length === 0) return;
    const previous = past[past.length - 1];
    syncNodeIdCounterFromNodes(previous.nodes);
    set({
      past: past.slice(0, -1),
      nodes: previous.nodes,
      edges: previous.edges,
      future: [{ nodes: [...nodes], edges: [...edges] }, ...future].slice(0, MAX_HISTORY),
      selectedNodeId: null,
      selectedEdgeId: null,
    });
  },

  redo: () => {
    const { future, nodes, edges, past } = get();
    if (future.length === 0) return;
    const next = future[0];
    syncNodeIdCounterFromNodes(next.nodes);
    set({
      future: future.slice(1),
      nodes: next.nodes,
      edges: next.edges,
      past: [...past, { nodes: [...nodes], edges: [...edges] }].slice(-MAX_HISTORY),
      selectedNodeId: null,
      selectedEdgeId: null,
    });
  },

  onNodesChange: (changes) => {
    // Snapshot once at the start of each drag gesture
    const dragging = get()._draggingNodeIds;
    const newDragging = new Set(dragging);
    let needsSnapshot = false;

    for (const change of changes) {
      if (change.type === "position") {
        if (change.dragging === true && !dragging.has(change.id)) {
          needsSnapshot = true;
          newDragging.add(change.id);
        } else if (!change.dragging) {
          newDragging.delete(change.id);
        }
      }
      // Snapshot before a node is removed via Delete key
      if (change.type === "remove") {
        needsSnapshot = true;
      }
    }

    if (needsSnapshot) get()._pushHistory();
    set({ nodes: applyNodeChanges(changes, get().nodes), _draggingNodeIds: newDragging });
  },

  onEdgesChange: (changes) => {
    // Snapshot before edge deletions
    if (changes.some((c) => c.type === "remove")) {
      get()._pushHistory();
    }
    set({ edges: applyEdgeChanges(changes, get().edges) });
  },

  onConnect: (connection) => {
    get()._pushHistory();

    const sourceNode = get().nodes.find((n) => n.id === connection.source);
    const isCondition =
      sourceNode?.data?.nodeCategory === "logic" &&
      sourceNode?.data?.label === "Condition";

    // CYCLIC-01.d — if the user drags a connection from a node back
    // to one of its ancestors in the existing forward subgraph, we
    // flip the new edge to ``type: "loopback"`` automatically. Gives
    // the one-click authoring surface without making the user hunt
    // for a menu. Forward adjacency is computed over the current
    // forward-only edges (existing loopbacks excluded) so an earlier
    // cycle doesn't taint the ancestor check.
    const isAutoLoopback =
      !!connection.source &&
      !!connection.target &&
      _isAncestorOnForwardGraph(
        connection.target,
        connection.source,
        get().edges,
      );

    // React Flow's ``addEdge`` helper assigns the final ``id`` when the
    // passed-in shape omits it, so the ``Edge`` cast is safe despite
    // ``id`` being absent at this point. The ``unknown`` hop silences
    // TS2352 — the discriminated ``Edge`` union is too narrow for the
    // structural shape we hand React Flow.
    const edge: Edge = isAutoLoopback
      ? ({
          ...connection,
          type: "loopback",
          // Seed the default cap so the runtime + EdgeInspector have
          // something sensible to show. Author can tune it in the
          // inspector right after.
          data: { maxIterations: LOOPBACK_DEFAULT_MAX_ITERATIONS },
        } as unknown as Edge)
      : ({
          ...connection,
          label: isCondition
            ? connection.sourceHandle === "false"
              ? "No"
              : "Yes"
            : undefined,
          style: isCondition
            ? {
                stroke:
                  connection.sourceHandle === "false" ? "#ef4444" : "#22c55e",
                strokeWidth: 2,
              }
            : undefined,
          animated: isCondition ? true : false,
        } as Edge);
    set({ edges: addEdge(edge, get().edges) });
  },

  replaceGraph: (nodes, edges) => {
    syncNodeIdCounterFromNodes(nodes);
    // Loading a saved/example workflow resets history
    set({ nodes, edges, selectedNodeId: null, selectedEdgeId: null, past: [], future: [] });
  },

  addNode: (nodeCategory, label, position, defaultConfig = {}) => {
    get()._pushHistory();
    const id = nextId();
    const newNode: Node = {
      id,
      type: "agenticNode",
      position,
      data: {
        label,
        nodeCategory,
        config: { ...defaultConfig },
        status: "idle",
      } satisfies AgenticNodeData,
    };
    set({ nodes: [...get().nodes, newNode], selectedNodeId: id });
  },

  selectNode: (id) => {
    // CYCLIC-01.d — node and edge selection are mutually exclusive so
    // the right-hand pane only ever has one inspector to render.
    set({ selectedNodeId: id, selectedEdgeId: id ? null : get().selectedEdgeId });
  },

  selectEdge: (id) => {
    set({ selectedEdgeId: id, selectedNodeId: id ? null : get().selectedNodeId });
  },

  updateEdge: (id, patch) => {
    get()._pushHistory();
    set({
      edges: get().edges.map((edge) =>
        edge.id === id ? { ...edge, ...patch, id: edge.id } : edge,
      ),
    });
  },

  updateNodeData: (id, data) => {
    set({
      nodes: get().nodes.map((node) =>
        node.id === id
          ? { ...node, data: { ...node.data, ...data } }
          : node,
      ),
    });
  },

  addStickyNote: (position) => {
    get()._pushHistory();
    // Separate id counter space to avoid colliding with node_N — sticky
    // ids are easy to spot in graph_json for debugging.
    const id = `sticky_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
    const newNode: Node = {
      id,
      type: "stickyNote",
      position,
      width: 220,
      height: 140,
      data: { ...STICKY_NOTE_DEFAULT_DATA },
    };
    set({ nodes: [...get().nodes, newNode], selectedNodeId: id });
    return id;
  },

  deleteNode: (id) => {
    get()._pushHistory();
    const remainingEdges = get().edges.filter(
      (e) => e.source !== id && e.target !== id,
    );
    const selectedEdgeStillValid = remainingEdges.some(
      (e) => e.id === get().selectedEdgeId,
    );
    set({
      nodes: get().nodes.filter((n) => n.id !== id),
      edges: remainingEdges,
      selectedNodeId:
        get().selectedNodeId === id ? null : get().selectedNodeId,
      selectedEdgeId: selectedEdgeStillValid ? get().selectedEdgeId : null,
    });
  },

  // ------------------------------------------------------------------
  // COPILOT-02.ii.b — preview mode
  // ------------------------------------------------------------------

  setCopilotPreview: (draftGraph, baseGraph) => {
    if (!draftGraph) {
      set({ copilotPreview: null });
      return;
    }
    const preview = _buildCopilotPreview(draftGraph, baseGraph);
    set({ copilotPreview: preview });
  },

  clearCopilotPreview: () => {
    set({ copilotPreview: null });
  },
}));


// ---------------------------------------------------------------------------
// COPILOT-02.ii.b — diff helper
// ---------------------------------------------------------------------------


/**
 * Build the ``CopilotPreviewGraph`` by comparing ``draftGraph`` against
 * ``baseGraph``. Nodes present in draft but not in base → ``added``.
 * Nodes in both but with different ``data.config`` → ``modified``.
 * Nodes in both with matching config → ``unchanged``. Nodes in base
 * but missing from draft are not surfaced here (they'd render as
 * ghost-deleted, which 02.ii.b defers to a later slice).
 *
 * Returned nodes are copied with ``data.__copilotDiff`` set — the
 * original draft graph is not mutated.
 */
function _buildCopilotPreview(
  draftGraph: { nodes: unknown[]; edges: unknown[] },
  baseGraph: { nodes: unknown[]; edges: unknown[] } | null,
): CopilotPreviewGraph {
  const baseNodes = (baseGraph?.nodes ?? []) as Node[];
  const baseEdges = hydrateEdgesFromLoad((baseGraph?.edges ?? []) as Edge[]);
  const draftNodes = (draftGraph.nodes ?? []) as Node[];
  // CYCLIC-01.d — hydrate loopback ``maxIterations`` into ``data``
  // before the preview renders so the LoopbackEdge chip shows the
  // author's configured cap, not the default.
  const draftEdges = hydrateEdgesFromLoad((draftGraph.edges ?? []) as Edge[]);

  const baseNodeById = new Map<string, Node>();
  for (const n of baseNodes) {
    if (typeof n.id === "string") baseNodeById.set(n.id, n);
  }
  const baseEdgeIds = new Set<string>();
  for (const e of baseEdges) {
    if (typeof e.id === "string") baseEdgeIds.add(e.id);
  }

  const addedNodeIds: string[] = [];
  const modifiedNodeIds: string[] = [];

  const annotated: Node[] = draftNodes.map((n) => {
    const base = baseNodeById.get(n.id);
    let status: CopilotDiffStatus;
    if (!base) {
      status = "added";
      addedNodeIds.push(n.id);
    } else if (_nodesDiffer(base, n)) {
      status = "modified";
      modifiedNodeIds.push(n.id);
    } else {
      status = "unchanged";
    }
    return {
      ...n,
      // xyflow respects the `draggable` flag per-node. Preview is
      // read-only, so we disable dragging at the node level too —
      // the FlowCanvas-level flags are a backstop.
      draggable: false,
      selectable: true,
      data: {
        ...(n.data as Record<string, unknown>),
        __copilotDiff: status,
      },
    };
  });

  const addedEdgeIds: string[] = [];
  const annotatedEdges: Edge[] = draftEdges.map((e) => {
    const isNew = !baseEdgeIds.has(e.id);
    if (isNew) addedEdgeIds.push(e.id);
    return {
      ...e,
      animated: isNew,
      style: {
        ...(e.style ?? {}),
        strokeDasharray: isNew ? "6 4" : undefined,
      },
    };
  });

  return {
    nodes: annotated,
    edges: annotatedEdges,
    addedNodeIds,
    modifiedNodeIds,
    addedEdgeIds,
  };
}


// ---------------------------------------------------------------------------
// CYCLIC-01.d — onConnect ancestor detection
// ---------------------------------------------------------------------------


/**
 * BFS from ``startId`` over the forward-only edge graph — i.e.
 * ignoring edges already typed as ``"loopback"`` so prior cycles in
 * the canvas don't taint reachability. Returns true if ``targetId``
 * is reachable. Used by ``onConnect`` to decide whether a new edge
 * is actually a back-reference (target == an ancestor of source) and
 * should therefore be created as a loopback.
 *
 * We walk the graph ``(forwardTarget) -> (forwardSource)`` — i.e.
 * reverse edges — so "is A an ancestor of B" becomes "can we reach A
 * by walking upstream from B". That's exactly the test we need for
 * the auto-loopback affordance.
 */
function _isAncestorOnForwardGraph(
  ancestorId: string,
  startId: string,
  edges: Edge[],
): boolean {
  if (ancestorId === startId) return false;
  // Build reverse adjacency: target -> list of sources, forward edges only.
  const reverse = new Map<string, string[]>();
  for (const e of edges) {
    if (e.type === "loopback") continue;
    if (!e.source || !e.target) continue;
    const bucket = reverse.get(e.target);
    if (bucket) bucket.push(e.source);
    else reverse.set(e.target, [e.source]);
  }
  const seen = new Set<string>([startId]);
  const queue: string[] = [startId];
  while (queue.length) {
    const cur = queue.shift()!;
    const parents = reverse.get(cur);
    if (!parents) continue;
    for (const p of parents) {
      if (p === ancestorId) return true;
      if (seen.has(p)) continue;
      seen.add(p);
      queue.push(p);
    }
  }
  return false;
}


function _nodesDiffer(a: Node, b: Node): boolean {
  // Compare config + label + displayName. Position changes count as
  // "unchanged" for diff purposes (the copilot might re-layout
  // without really mutating the semantic graph).
  const aData = (a.data ?? {}) as Record<string, unknown>;
  const bData = (b.data ?? {}) as Record<string, unknown>;
  if (aData.label !== bData.label) return true;
  if (aData.displayName !== bData.displayName) return true;
  const aCfg = JSON.stringify(aData.config ?? {});
  const bCfg = JSON.stringify(bData.config ?? {});
  return aCfg !== bCfg;
}
