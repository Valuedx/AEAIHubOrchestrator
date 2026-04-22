import { describe, it, expect, beforeEach } from "vitest";
import { useFlowStore } from "./flowStore";

describe("flowStore undo/redo", () => {
  beforeEach(() => {
    // Fresh store between tests
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
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
