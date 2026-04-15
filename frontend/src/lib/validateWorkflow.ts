/**
 * validateWorkflow.ts
 *
 * Pre-run validation for the workflow canvas.
 * Returns a list of ValidationError objects that can be shown to the user
 * before execution starts. Errors block execution; warnings allow it with
 * a confirmation prompt.
 *
 * Checks performed:
 *  1. At least one trigger node exists
 *  2. All nodes are reachable from a trigger (disconnected node detection)
 *  3. Required fields are non-empty for specific node types
 *  4. Node ID cross-references (responseNodeId, historyNodeId) point to real nodes
 */

import type { Node, Edge } from "@xyflow/react";
import { nodeCanvasTitle, type AgenticNodeData } from "@/types/nodes";

export interface ValidationError {
  nodeId: string;
  nodeLabel: string;
  message: string;
  severity: "error" | "warning";
}

// Fields that must be non-empty per node label (beyond schema defaults)
const REQUIRED_FIELDS: Record<string, string[]> = {
  "Condition":               ["condition"],
  "HTTP Request":            ["url"],
  "MCP Tool":                ["toolName"],
  "ForEach":                 ["arrayExpression"],
  "Save Conversation State": ["responseNodeId"],
  "LLM Router":              [],   // intents check done separately
  "Reflection":              ["reflectionPrompt"],
  "Loop":                    ["continueExpression"],
  "Bridge User Reply":       [], // at least one of messageExpression / responseNodeId — checked below
  "Notification":            ["channel", "destination", "messageTemplate"],
};

// Fields that reference another node ID by value — must exist in the graph
const NODE_ID_REF_FIELDS: Record<string, string[]> = {
  "Save Conversation State": ["responseNodeId"],
  "Bridge User Reply":       ["responseNodeId"],
  "LLM Router":              ["historyNodeId"],
  "Intent Classifier":       ["historyNodeId"],
  "Entity Extractor":        ["scopeFromNode"],
};

export function validateWorkflow(
  nodes: Node[],
  edges: Edge[],
): ValidationError[] {
  const errors: ValidationError[] = [];
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  // ── 1. Must have at least one trigger ──────────────────────────────────────
  const triggerNodes = nodes.filter(
    (n) => (n.data as AgenticNodeData).nodeCategory === "trigger",
  );
  if (triggerNodes.length === 0) {
    errors.push({
      nodeId: "",
      nodeLabel: "Workflow",
      message: "No trigger node found. Add a Webhook Trigger or Schedule Trigger to start the workflow.",
      severity: "error",
    });
  }

  // ── 2. Reachability — BFS from all trigger nodes ───────────────────────────
  if (triggerNodes.length > 0 && nodes.length > 1) {
    const reachable = new Set<string>();
    const queue = triggerNodes.map((n) => n.id);
    for (const id of queue) reachable.add(id);

    while (queue.length > 0) {
      const current = queue.shift()!;
      for (const edge of edges) {
        if (edge.source === current && !reachable.has(edge.target)) {
          reachable.add(edge.target);
          queue.push(edge.target);
        }
      }
    }

    for (const node of nodes) {
      if (!reachable.has(node.id)) {
        const data = node.data as AgenticNodeData;
        const title = nodeCanvasTitle(data);
        errors.push({
          nodeId: node.id,
          nodeLabel: title,
          message: `"${title}" (${node.id}) is not connected to any trigger. Either connect it or remove it.`,
          severity: "warning",
        });
      }
    }
  }

  // ── 3. Required field checks ───────────────────────────────────────────────
  for (const node of nodes) {
    const data = node.data as AgenticNodeData;
    const title = nodeCanvasTitle(data);
    const requiredFields = REQUIRED_FIELDS[data.label];

    if (requiredFields) {
      for (const field of requiredFields) {
        const val = (data.config as Record<string, unknown>)[field];
        if (val === undefined || val === null || val === "") {
          errors.push({
            nodeId: node.id,
            nodeLabel: title,
            message: `"${title}" (${node.id}): field "${field}" is required but empty.`,
            severity: "error",
          });
        }
      }
    }

    // Intent Classifier: intents array must have ≥ 1 entry, each with a name
    if (data.label === "Intent Classifier") {
      const intents = (data.config as Record<string, unknown>).intents;
      if (!Array.isArray(intents) || intents.length === 0) {
        errors.push({
          nodeId: node.id,
          nodeLabel: title,
          message: `"${title}" (${node.id}): "intents" must have at least one intent.`,
          severity: "error",
        });
      } else {
        for (let i = 0; i < intents.length; i++) {
          const it = intents[i] as Record<string, unknown> | undefined;
          if (!it || !it.name || String(it.name).trim() === "") {
            errors.push({
              nodeId: node.id,
              nodeLabel: title,
              message: `"${title}" (${node.id}): intent at index ${i} has no "name".`,
              severity: "error",
            });
          }
        }
      }
    }

    // Entity Extractor: entities array must have ≥ 1 entry, each with a name
    if (data.label === "Entity Extractor") {
      const entities = (data.config as Record<string, unknown>).entities;
      if (!Array.isArray(entities) || entities.length === 0) {
        errors.push({
          nodeId: node.id,
          nodeLabel: title,
          message: `"${title}" (${node.id}): "entities" must have at least one entity.`,
          severity: "error",
        });
      } else {
        for (let i = 0; i < entities.length; i++) {
          const ent = entities[i] as Record<string, unknown> | undefined;
          if (!ent || !ent.name || String(ent.name).trim() === "") {
            errors.push({
              nodeId: node.id,
              nodeLabel: title,
              message: `"${title}" (${node.id}): entity at index ${i} has no "name".`,
              severity: "error",
            });
          }
        }
      }
    }

    // LLM Router: intents array must have ≥ 1 entry
    if (data.label === "LLM Router") {
      const intents = (data.config as Record<string, unknown>).intents;
      if (!Array.isArray(intents) || intents.length === 0) {
        errors.push({
          nodeId: node.id,
          nodeLabel: title,
          message: `"${title}" (${node.id}): "intents" must have at least one intent label.`,
          severity: "error",
        });
      }
    }

    if (data.label === "Bridge User Reply") {
      const cfg = data.config as Record<string, unknown>;
      const expr = String(cfg.messageExpression ?? "").trim();
      const rid = String(cfg.responseNodeId ?? "").trim();
      if (!expr && !rid) {
        errors.push({
          nodeId: node.id,
          nodeLabel: title,
          message: `"${title}" (${node.id}): set messageExpression or responseNodeId (at least one).`,
          severity: "error",
        });
      }
    }

    // Loop: warn if maxIterations exceeds the backend hard cap of 25
    if (data.label === "Loop") {
      const maxIter = (data.config as Record<string, unknown>).maxIterations;
      if (typeof maxIter === "number" && maxIter > 25) {
        errors.push({
          nodeId: node.id,
          nodeLabel: title,
          message: `"${title}" (${node.id}): maxIterations is ${maxIter} but the backend hard cap is 25 — the loop will stop at 25.`,
          severity: "warning",
        });
      }
    }
  }

  // ── 4. Node ID cross-reference validation ─────────────────────────────────
  for (const node of nodes) {
    const data = node.data as AgenticNodeData;
    const refFields = NODE_ID_REF_FIELDS[data.label];

    if (refFields) {
      const title = nodeCanvasTitle(data);
      for (const field of refFields) {
        const refId = (data.config as Record<string, unknown>)[field] as string | undefined;
        // Only validate if a non-empty value was provided
        if (refId && refId.trim() !== "") {
          if (!nodeMap.has(refId.trim())) {
            errors.push({
              nodeId: node.id,
              nodeLabel: title,
              message: `"${title}" (${node.id}): field "${field}" references "${refId}" which does not exist on the canvas.`,
              severity: "error",
            });
          }
        }
      }
    }
  }

  return errors;
}
