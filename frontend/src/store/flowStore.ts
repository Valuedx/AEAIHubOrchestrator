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

interface FlowState {
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;

  /** Undo/redo history stacks */
  past: Snapshot[];
  future: Snapshot[];
  /** Tracks node IDs currently being dragged so we snapshot once per drag */
  _draggingNodeIds: Set<string>;

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
  updateNodeData: (
    id: string,
    data: Partial<AgenticNodeData> | Partial<StickyNoteData>,
  ) => void;
  deleteNode: (id: string) => void;

  /** DV-03 — add a sticky note at the given canvas position. Returns
   *  the new node's id so callers can scroll / select it. */
  addStickyNote: (position: XYPosition) => string;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  past: [],
  future: [],
  _draggingNodeIds: new Set(),

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

    const edge = {
      ...connection,
      label: isCondition ? (connection.sourceHandle === "false" ? "No" : "Yes") : undefined,
      style: isCondition
        ? { stroke: connection.sourceHandle === "false" ? "#ef4444" : "#22c55e", strokeWidth: 2 }
        : undefined,
      animated: isCondition ? true : false,
    };
    set({ edges: addEdge(edge, get().edges) });
  },

  replaceGraph: (nodes, edges) => {
    syncNodeIdCounterFromNodes(nodes);
    // Loading a saved/example workflow resets history
    set({ nodes, edges, selectedNodeId: null, past: [], future: [] });
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
    set({ selectedNodeId: id });
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
    set({
      nodes: get().nodes.filter((n) => n.id !== id),
      edges: get().edges.filter((e) => e.source !== id && e.target !== id),
      selectedNodeId:
        get().selectedNodeId === id ? null : get().selectedNodeId,
    });
  },
}));
