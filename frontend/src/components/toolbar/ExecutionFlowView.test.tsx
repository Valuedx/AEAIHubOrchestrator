/**
 * Smoke tests for the FV-02 ExecutionFlowView.
 *
 * React Flow rendering itself needs a non-trivial DOM layout which
 * jsdom can't provide — so we don't assert on node positions. The
 * tests here lock the two cheap but important properties:
 *
 *   1. Empty graph renders a sensible fallback instead of blowing up.
 *   2. Non-empty graph mounts without throwing (covers the
 *      ReactFlowProvider wiring + nodeTypes reference stability).
 */

import { describe, expect, it, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { ExecutionFlowView } from "./ExecutionFlowView";
import { useFlowStore } from "@/store/flowStore";

describe("ExecutionFlowView", () => {
  beforeEach(() => {
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      past: [],
      future: [],
      _draggingNodeIds: new Set(),
    });
  });

  it("renders an empty-state hint when the graph has no nodes", () => {
    render(<ExecutionFlowView />);
    expect(
      screen.getByText(/Load or build a workflow on the canvas/i),
    ).toBeInTheDocument();
  });

  it("mounts React Flow when the graph has nodes (no throw)", () => {
    useFlowStore.getState().addNode("agent", "LLM Agent", { x: 0, y: 0 });
    useFlowStore.getState().addNode("action", "MCP Tool", { x: 200, y: 0 });
    const { container } = render(<ExecutionFlowView />);
    // jsdom can't fully render the React Flow pane, but the provider +
    // wrapper should still mount. Assert by checking that the empty-
    // state hint is NOT shown.
    expect(
      container.textContent ?? "",
    ).not.toMatch(/Load or build a workflow on the canvas/i);
  });
});
