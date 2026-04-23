import { describe, it, expect, beforeEach } from "vitest";
import { useFlowStore } from "./flowStore";

describe("flowStore undo/redo", () => {
  beforeEach(() => {
    // Fresh store between tests
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      selectedEdgeId: null,
      past: [],
      future: [],
      _draggingNodeIds: new Set(),
    });
  });

  it("addNode pushes to history so undo removes the node", () => {
    useFlowStore.getState().addNode("agent", "LLM Agent", { x: 0, y: 0 });
    expect(useFlowStore.getState().nodes).toHaveLength(1);

    useFlowStore.getState().undo();
    expect(useFlowStore.getState().nodes).toHaveLength(0);
  });

  it("redo replays the undone addNode", () => {
    const { addNode, undo, redo } = useFlowStore.getState();
    addNode("agent", "LLM Agent", { x: 0, y: 0 });
    undo();
    redo();
    expect(useFlowStore.getState().nodes).toHaveLength(1);
    expect(useFlowStore.getState().nodes[0].data.label).toBe("LLM Agent");
  });

  it("undo is a no-op with an empty history", () => {
    useFlowStore.getState().undo();
    expect(useFlowStore.getState().nodes).toEqual([]);
    expect(useFlowStore.getState().past).toEqual([]);
  });

  it("redo is a no-op with an empty future", () => {
    useFlowStore.getState().addNode("agent", "LLM Agent", { x: 0, y: 0 });
    useFlowStore.getState().redo();
    // redo with no future should leave state unchanged
    expect(useFlowStore.getState().nodes).toHaveLength(1);
  });

  it("a new action after undo clears the redo stack", () => {
    const { addNode, undo } = useFlowStore.getState();
    addNode("agent", "LLM Agent", { x: 0, y: 0 });
    addNode("action", "MCP Tool", { x: 100, y: 0 });
    undo(); // one node left in past, one in future
    expect(useFlowStore.getState().future).toHaveLength(1);
    addNode("logic", "Condition", { x: 200, y: 0 });
    expect(useFlowStore.getState().future).toHaveLength(0);
  });

  it("deleteNode is undoable and restores the node", () => {
    const { addNode, deleteNode, undo } = useFlowStore.getState();
    addNode("agent", "LLM Agent", { x: 0, y: 0 });
    const id = useFlowStore.getState().nodes[0].id;
    deleteNode(id);
    expect(useFlowStore.getState().nodes).toHaveLength(0);
    undo();
    expect(useFlowStore.getState().nodes).toHaveLength(1);
    expect(useFlowStore.getState().nodes[0].id).toBe(id);
  });

  it("replaceGraph wipes history (loading a saved workflow is not undoable)", () => {
    const { addNode, replaceGraph } = useFlowStore.getState();
    addNode("agent", "LLM Agent", { x: 0, y: 0 });
    replaceGraph([], []);
    expect(useFlowStore.getState().past).toEqual([]);
    expect(useFlowStore.getState().future).toEqual([]);
  });

  it("caps history at MAX_HISTORY (50) entries", () => {
    const { addNode } = useFlowStore.getState();
    for (let i = 0; i < 60; i++) {
      addNode("agent", "LLM Agent", { x: i, y: 0 });
    }
    // 60 add actions → each pushes one snapshot, but past is capped at 50
    expect(useFlowStore.getState().past.length).toBeLessThanOrEqual(50);
    expect(useFlowStore.getState().nodes).toHaveLength(60);
  });
});


// ---------------------------------------------------------------------------
// COPILOT-02.ii.b — copilotPreview diff mode
// ---------------------------------------------------------------------------


describe("flowStore copilot preview", () => {
  beforeEach(() => {
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      selectedEdgeId: null,
      past: [],
      future: [],
      _draggingNodeIds: new Set(),
      copilotPreview: null,
    });
  });

  const baseGraph = {
    nodes: [
      {
        id: "node_1",
        type: "agenticNode",
        position: { x: 0, y: 0 },
        data: { label: "Webhook", config: { path: "/in" } },
      },
    ],
    edges: [],
  };

  it("setCopilotPreview flags brand-new nodes as 'added'", () => {
    const draft = {
      nodes: [
        ...baseGraph.nodes,
        {
          id: "node_2",
          type: "agenticNode",
          position: { x: 200, y: 0 },
          data: { label: "LLM Agent", config: {} },
        },
      ],
      edges: [
        { id: "e1", source: "node_1", target: "node_2" },
      ],
    };

    useFlowStore.getState().setCopilotPreview(draft, baseGraph);
    const preview = useFlowStore.getState().copilotPreview;

    expect(preview).not.toBeNull();
    expect(preview!.addedNodeIds).toEqual(["node_2"]);
    expect(preview!.modifiedNodeIds).toEqual([]);
    expect(preview!.addedEdgeIds).toEqual(["e1"]);
    const addedNode = preview!.nodes.find((n) => n.id === "node_2")!;
    expect((addedNode.data as Record<string, unknown>).__copilotDiff).toBe("added");
    // Unchanged node carries the "unchanged" marker — useful for
    // future subsystems that want to render it explicitly.
    const baseNode = preview!.nodes.find((n) => n.id === "node_1")!;
    expect((baseNode.data as Record<string, unknown>).__copilotDiff).toBe("unchanged");
  });

  it("detects config changes as 'modified' not 'added'", () => {
    const draft = {
      nodes: [
        {
          id: "node_1",
          type: "agenticNode",
          position: { x: 0, y: 0 },
          data: { label: "Webhook", config: { path: "/new-in" } },
        },
      ],
      edges: [],
    };

    useFlowStore.getState().setCopilotPreview(draft, baseGraph);
    const preview = useFlowStore.getState().copilotPreview;
    expect(preview!.addedNodeIds).toEqual([]);
    expect(preview!.modifiedNodeIds).toEqual(["node_1"]);
    const n = preview!.nodes[0];
    expect((n.data as Record<string, unknown>).__copilotDiff).toBe("modified");
  });

  it("treats all nodes as 'added' for a net-new draft (no base)", () => {
    const draft = {
      nodes: [
        {
          id: "node_1",
          type: "agenticNode",
          position: { x: 0, y: 0 },
          data: { label: "Webhook", config: {} },
        },
        {
          id: "node_2",
          type: "agenticNode",
          position: { x: 100, y: 0 },
          data: { label: "LLM Agent", config: {} },
        },
      ],
      edges: [{ id: "e1", source: "node_1", target: "node_2" }],
    };

    useFlowStore.getState().setCopilotPreview(draft, null);
    const preview = useFlowStore.getState().copilotPreview;
    expect(preview!.addedNodeIds).toEqual(["node_1", "node_2"]);
    expect(preview!.modifiedNodeIds).toEqual([]);
    expect(preview!.addedEdgeIds).toEqual(["e1"]);
  });

  it("position-only changes do NOT mark a node as modified", () => {
    // The copilot may re-layout without really mutating the graph;
    // we don't want that to paint every node as an 'edit'.
    const draft = {
      nodes: [
        {
          id: "node_1",
          type: "agenticNode",
          position: { x: 999, y: 999 }, // moved
          data: { label: "Webhook", config: { path: "/in" } },
        },
      ],
      edges: [],
    };
    useFlowStore.getState().setCopilotPreview(draft, baseGraph);
    const preview = useFlowStore.getState().copilotPreview;
    expect(preview!.modifiedNodeIds).toEqual([]);
    expect((preview!.nodes[0].data as Record<string, unknown>).__copilotDiff).toBe("unchanged");
  });

  it("disables drag on preview nodes even when selection stays on", () => {
    const draft = {
      nodes: [
        { id: "node_1", type: "agenticNode", position: { x: 0, y: 0 },
          data: { label: "X", config: {} } },
      ],
      edges: [],
    };
    useFlowStore.getState().setCopilotPreview(draft, null);
    const preview = useFlowStore.getState().copilotPreview;
    expect(preview!.nodes[0].draggable).toBe(false);
    expect(preview!.nodes[0].selectable).toBe(true);
  });

  it("clearCopilotPreview resets to null and doesn't touch editable nodes/edges", () => {
    // Seed real editable nodes on the store…
    useFlowStore.getState().addNode("agent", "LLM Agent", { x: 0, y: 0 });
    const editableCount = useFlowStore.getState().nodes.length;

    useFlowStore.getState().setCopilotPreview(
      { nodes: [{ id: "preview-a", type: "agenticNode",
                  position: { x: 0, y: 0 }, data: { label: "X", config: {} } }],
        edges: [] },
      null,
    );
    expect(useFlowStore.getState().copilotPreview).not.toBeNull();
    // editable nodes untouched
    expect(useFlowStore.getState().nodes).toHaveLength(editableCount);

    useFlowStore.getState().clearCopilotPreview();
    expect(useFlowStore.getState().copilotPreview).toBeNull();
    expect(useFlowStore.getState().nodes).toHaveLength(editableCount);
  });

  it("passing null to setCopilotPreview clears preview", () => {
    useFlowStore.getState().setCopilotPreview(
      { nodes: [], edges: [] }, null,
    );
    expect(useFlowStore.getState().copilotPreview).not.toBeNull();
    useFlowStore.getState().setCopilotPreview(null, null);
    expect(useFlowStore.getState().copilotPreview).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// CYCLIC-01.d — loopback auto-detect on connect + edge selection
// ---------------------------------------------------------------------------


describe("flowStore loopback edges", () => {
  beforeEach(() => {
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      selectedEdgeId: null,
      past: [],
      future: [],
      _draggingNodeIds: new Set(),
      copilotPreview: null,
    });
  });

  function seedChain() {
    // a → b → c, no cycles.
    useFlowStore.setState({
      nodes: [
        { id: "a", type: "agenticNode", position: { x: 0, y: 0 }, data: { label: "A" } },
        { id: "b", type: "agenticNode", position: { x: 100, y: 0 }, data: { label: "B" } },
        { id: "c", type: "agenticNode", position: { x: 200, y: 0 }, data: { label: "C" } },
      ],
      edges: [
        { id: "ea", source: "a", target: "b" },
        { id: "eb", source: "b", target: "c" },
      ],
    });
  }

  it("onConnect auto-flags a back-reference as loopback", () => {
    seedChain();
    // Drag from c back to a — a IS an ancestor of c, so this is a cycle.
    useFlowStore.getState().onConnect({
      source: "c",
      target: "a",
      sourceHandle: null,
      targetHandle: null,
    });
    const edges = useFlowStore.getState().edges;
    const newEdge = edges.find((e) => e.source === "c" && e.target === "a");
    expect(newEdge).toBeDefined();
    expect(newEdge!.type).toBe("loopback");
    // Defaulted cap under data for the LoopbackEdge renderer.
    expect((newEdge!.data as { maxIterations?: number } | undefined)?.maxIterations)
      .toBe(10);
  });

  it("onConnect leaves forward connections as forward edges", () => {
    seedChain();
    // a → c is a forward skip, not a back-reference — target (c)
    // is NOT an ancestor of source (a).
    useFlowStore.getState().onConnect({
      source: "a",
      target: "c",
      sourceHandle: null,
      targetHandle: null,
    });
    const edges = useFlowStore.getState().edges;
    const newEdge = edges.find((e) => e.source === "a" && e.target === "c");
    expect(newEdge).toBeDefined();
    expect(newEdge!.type).toBeUndefined();
  });

  it("existing loopback edges don't taint ancestor detection", () => {
    // Graph: a → b → c, plus a prior loopback c → a.
    // Now add b → a — target (a) IS an ancestor of b on the forward
    // subgraph, so this new edge should also be flagged loopback.
    seedChain();
    useFlowStore.setState({
      edges: [
        ...useFlowStore.getState().edges,
        {
          id: "lb1",
          source: "c",
          target: "a",
          type: "loopback",
          data: { maxIterations: 5 },
        },
      ],
    });
    useFlowStore.getState().onConnect({
      source: "b",
      target: "a",
      sourceHandle: null,
      targetHandle: null,
    });
    const newEdge = useFlowStore
      .getState()
      .edges.find((e) => e.source === "b" && e.target === "a");
    expect(newEdge!.type).toBe("loopback");
  });

  it("selectEdge + selectNode are mutually exclusive", () => {
    const s = useFlowStore.getState();
    s.selectNode("node_1");
    expect(useFlowStore.getState().selectedNodeId).toBe("node_1");
    expect(useFlowStore.getState().selectedEdgeId).toBeNull();

    s.selectEdge("edge_1");
    expect(useFlowStore.getState().selectedEdgeId).toBe("edge_1");
    expect(useFlowStore.getState().selectedNodeId).toBeNull();

    s.selectNode("node_2");
    expect(useFlowStore.getState().selectedNodeId).toBe("node_2");
    expect(useFlowStore.getState().selectedEdgeId).toBeNull();
  });

  it("updateEdge merges a partial patch and pushes history", () => {
    useFlowStore.setState({
      edges: [
        {
          id: "lb1",
          source: "b",
          target: "a",
          type: "loopback",
          data: { maxIterations: 10 },
        },
      ],
    });
    useFlowStore.getState().updateEdge("lb1", {
      data: { maxIterations: 42 },
    });
    const edge = useFlowStore.getState().edges[0];
    expect(
      (edge.data as { maxIterations?: number }).maxIterations,
    ).toBe(42);
    // Undo stack got a snapshot.
    expect(useFlowStore.getState().past.length).toBeGreaterThan(0);
  });

  it("deleteNode clears selectedEdgeId when the edge attached to the node vanishes", () => {
    seedChain();
    useFlowStore.getState().selectEdge("ea"); // a → b
    useFlowStore.getState().deleteNode("a");
    expect(useFlowStore.getState().selectedEdgeId).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// CYCLIC-01.d — graph_json interop (serialise/hydrate)
// ---------------------------------------------------------------------------


describe("edge graph_json interop", () => {
  it("serialiseEdgesForSave lifts data.maxIterations to top-level", async () => {
    const { serialiseEdgesForSave } = await import("@/types/edges");
    const out = serialiseEdgesForSave([
      {
        id: "lb1", source: "b", target: "a", type: "loopback",
        data: { maxIterations: 7 },
      },
      { id: "fwd", source: "a", target: "b" },
    ]);
    expect(out[0].maxIterations).toBe(7);
    expect(out[1].maxIterations).toBeUndefined();
  });

  it("hydrateEdgesFromLoad drops top-level maxIterations into data", async () => {
    const { hydrateEdgesFromLoad } = await import("@/types/edges");
    const out = hydrateEdgesFromLoad([
      {
        id: "lb1", source: "b", target: "a", type: "loopback",
        maxIterations: 42,
      },
      { id: "fwd", source: "a", target: "b" },
    ]);
    expect((out[0].data as { maxIterations: number }).maxIterations).toBe(42);
    expect(out[1].data).toBeUndefined();
  });

  it("round-trip preserves the cap", async () => {
    const { serialiseEdgesForSave, hydrateEdgesFromLoad } = await import("@/types/edges");
    const initial = [
      {
        id: "lb1", source: "b", target: "a", type: "loopback",
        data: { maxIterations: 25 },
      },
    ];
    const saved = serialiseEdgesForSave(initial);
    const hydrated = hydrateEdgesFromLoad(saved);
    expect(
      (hydrated[0].data as { maxIterations: number }).maxIterations,
    ).toBe(25);
  });
});
