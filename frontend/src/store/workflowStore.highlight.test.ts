/**
 * FV-03 — highlightNode state machine.
 *
 * Covers the cross-surface bridge that lets Flow ↔ Logs signal "focus
 * this node" to each other. The two invariants we want locked down:
 *
 *   1. Firing highlightNode(X) sets highlightedNodeId and clears it
 *      after HIGHLIGHT_DURATION_MS.
 *   2. Firing highlightNode(Y) between the set and the scheduled clear
 *      re-owns the window — the new node stays highlighted for a full
 *      HIGHLIGHT_DURATION_MS, not a leftover partial interval.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HIGHLIGHT_DURATION_MS,
  useWorkflowStore,
} from "./workflowStore";


describe("highlightNode", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useWorkflowStore.setState({ highlightedNodeId: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sets highlightedNodeId immediately", () => {
    useWorkflowStore.getState().highlightNode("node_3");
    expect(useWorkflowStore.getState().highlightedNodeId).toBe("node_3");
  });

  it("clears highlightedNodeId after HIGHLIGHT_DURATION_MS", () => {
    useWorkflowStore.getState().highlightNode("node_3");
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS - 1);
    expect(useWorkflowStore.getState().highlightedNodeId).toBe("node_3");
    vi.advanceTimersByTime(1);
    expect(useWorkflowStore.getState().highlightedNodeId).toBeNull();
  });

  it("re-owns the window when a second highlight fires mid-interval", () => {
    useWorkflowStore.getState().highlightNode("node_3");
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS / 2);
    useWorkflowStore.getState().highlightNode("node_5");
    expect(useWorkflowStore.getState().highlightedNodeId).toBe("node_5");

    // The original timer would have fired at this point if it were still
    // pending — advance just past it and confirm node_5 is still set.
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS / 2 + 1);
    expect(useWorkflowStore.getState().highlightedNodeId).toBe("node_5");

    // After a full fresh HIGHLIGHT_DURATION_MS from the second call, the
    // clear runs as expected.
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS / 2);
    expect(useWorkflowStore.getState().highlightedNodeId).toBeNull();
  });

  it("does not clear a fresh highlight when a stale timer finally fires", () => {
    // Highlight A, then quickly swap to B. The A timer is cleared — but
    // even if we simulate the interleaving, the B highlight must survive
    // because the clear path checks that the pending node matches.
    useWorkflowStore.getState().highlightNode("a");
    useWorkflowStore.getState().highlightNode("b");
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS - 10);
    expect(useWorkflowStore.getState().highlightedNodeId).toBe("b");
    vi.advanceTimersByTime(10);
    expect(useWorkflowStore.getState().highlightedNodeId).toBeNull();
  });

  it("highlighting the same node twice resets the timer (no flicker)", () => {
    useWorkflowStore.getState().highlightNode("node_3");
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS - 100);
    useWorkflowStore.getState().highlightNode("node_3");
    // Another HIGHLIGHT_DURATION_MS - 100 passes; the stale timer would
    // have fired already if it weren't cleared.
    vi.advanceTimersByTime(HIGHLIGHT_DURATION_MS - 100);
    expect(useWorkflowStore.getState().highlightedNodeId).toBe("node_3");
    vi.advanceTimersByTime(100);
    expect(useWorkflowStore.getState().highlightedNodeId).toBeNull();
  });
});
