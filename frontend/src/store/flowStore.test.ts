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
