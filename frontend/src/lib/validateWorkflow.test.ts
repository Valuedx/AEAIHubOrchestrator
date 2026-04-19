import { describe, it, expect } from "vitest";
import type { Node, Edge } from "@xyflow/react";
import { validateWorkflow } from "./validateWorkflow";
import type { AgenticNodeData, NodeCategory } from "@/types/nodes";

function n(
  id: string,
  label: string,
  nodeCategory: NodeCategory,
  config: Record<string, unknown> = {},
): Node<AgenticNodeData> {
  return {
    id,
    type: "agenticNode",
    position: { x: 0, y: 0 },
    data: {
      label,
      nodeCategory,
      config,
      status: "idle",
    },
  };
}
function e(source: string, target: string, sourceHandle?: string): Edge {
  return { id: `${source}-${target}-${sourceHandle ?? ""}`, source, target, sourceHandle };
}

describe("validateWorkflow", () => {
  it("flags a workflow with no trigger", () => {
    const errs = validateWorkflow([n("a", "LLM Agent", "agent")], []);
    expect(errs.some((x) => x.severity === "error" && /trigger/i.test(x.message))).toBe(true);
  });

  it("passes a minimal linear trigger→agent", () => {
    const nodes = [
      n("t", "Webhook Trigger", "trigger"),
      n("a", "LLM Agent", "agent", { systemPrompt: "say hi" }),
    ];
    const edges = [e("t", "a")];
    const errs = validateWorkflow(nodes, edges);
    // Only warnings allowed, no errors
    expect(errs.filter((x) => x.severity === "error")).toEqual([]);
  });

  it("warns about unreachable nodes", () => {
    const nodes = [
      n("t", "Webhook Trigger", "trigger"),
      n("a", "LLM Agent", "agent"),
      n("orphan", "MCP Tool", "action", { toolName: "echo" }),
    ];
    const edges = [e("t", "a")];
    const errs = validateWorkflow(nodes, edges);
    expect(
      errs.find((x) => x.nodeId === "orphan" && x.severity === "warning"),
    ).toBeTruthy();
  });

  it("errors when Condition has no condition", () => {
    const nodes = [
      n("t", "Webhook Trigger", "trigger"),
      n("c", "Condition", "logic", {}),
    ];
    const edges = [e("t", "c")];
    const errs = validateWorkflow(nodes, edges);
    expect(
      errs.find((x) => x.nodeId === "c" && /condition/.test(x.message)),
    ).toBeTruthy();
  });

  it("errors when MCP Tool has no toolName", () => {
    const nodes = [
      n("t", "Webhook Trigger", "trigger"),
      n("m", "MCP Tool", "action", {}),
    ];
    const edges = [e("t", "m")];
    const errs = validateWorkflow(nodes, edges);
    expect(
      errs.find((x) => x.nodeId === "m" && /toolName/.test(x.message)),
    ).toBeTruthy();
  });

  it("errors on Intent Classifier with zero intents", () => {
    const nodes = [
      n("t", "Webhook Trigger", "trigger"),
      n("ic", "Intent Classifier", "nlp", { historyNodeId: "t", intents: [] }),
    ];
    const edges = [e("t", "ic")];
    const errs = validateWorkflow(nodes, edges);
    expect(errs.find((x) => x.nodeId === "ic" && /intent/i.test(x.message))).toBeTruthy();
  });
});
