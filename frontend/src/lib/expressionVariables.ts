/**
 * expressionVariables.ts
 *
 * Generates autocomplete suggestions for expression fields in the property panel.
 *
 * Three modes:
 *   "expression" — bare dot-path expressions used by safe_eval
 *                  e.g. node_2.intent == "diagnose"
 *   "nodeId"     — just the node ID, for responseNodeId / historyNodeId fields
 *                  e.g. node_3
 *   "jinja2"     — {{ }} wrapped, for systemPrompt templates
 *                  e.g. {{ trigger.message }}
 */

import type { Node } from "@xyflow/react";
import { nodeCanvasTitle, type AgenticNodeData } from "@/types/nodes";

export type ExpressionMode = "expression" | "nodeId" | "jinja2";

export interface ExpressionVariable {
  /** The string to be inserted into the input */
  value: string;
  /** Human-readable label shown in the dropdown */
  label: string;
  /** Group header shown above a cluster of suggestions */
  group: string;
}

// ---------------------------------------------------------------------------
// Known output fields per node label
// ---------------------------------------------------------------------------

const NODE_OUTPUT_FIELDS: Record<string, string[]> = {
  "LLM Agent":                ["response", "usage", "provider", "model", "memory_debug"],
  "ReAct Agent":              ["response", "iterations", "total_iterations", "usage", "memory_debug"],
  "LLM Router":               ["intent", "raw_response", "usage", "memory_debug"],
  "Reflection":               ["_raw_response"],   // user-defined outputKeys are also available but dynamic
  "MCP Tool":                 ["result"],
  "HTTP Request":             ["status_code", "body", "headers"],
  "Human Approval":           ["approved", "approver"],
  "Load Conversation State":  ["session_id", "session_ref_id", "messages", "message_count", "summary_text", "summary_through_turn"],
  "Save Conversation State":  ["saved", "session_id", "session_ref_id", "message_count", "summary_updated", "promoted_memory_records", "promoted_entity_facts"],
  "Condition":                [],
  "Merge":                    [],
  "ForEach":                  [],
  "Notification":             ["success", "channel", "status_code", "message_preview"],
  "Intent Classifier":        ["intents", "confidence", "fallback", "scores", "mode_used", "heuristic_scores"],
  "Entity Extractor":         ["entities", "missing_required", "extraction_method"],
};

// Trigger nodes don't output to node_X — they populate the `trigger` key
const TRIGGER_OUTPUT_FIELDS: Record<string, string[]> = {
  "Webhook Trigger":   ["body", "message", "session_id", "headers", "method", "path"],
  "Schedule Trigger":  ["scheduled_at", "cron"],
};

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export function getExpressionVariables(
  nodes: Node[],
  currentNodeId: string | null,
  mode: ExpressionMode,
): ExpressionVariable[] {
  const vars: ExpressionVariable[] = [];

  // _loop_item is only meaningful inside a ForEach body
  if (mode === "expression" && nodes.some((n) => (n.data as AgenticNodeData).label === "ForEach")) {
    vars.push({
      value: "_loop_item",
      label: "_loop_item  (ForEach iteration value)",
      group: "Loop",
    });
  }

  for (const node of nodes) {
    if (node.id === currentNodeId) continue; // skip self

    const data = node.data as AgenticNodeData;
    const label = data.label;
    const canvasTitle = nodeCanvasTitle(data);

    if (data.nodeCategory === "trigger") {
      // Triggers populate `trigger.*` in the context
      if (mode === "nodeId") continue; // trigger has no node ID to reference

      const fields = TRIGGER_OUTPUT_FIELDS[label] ?? ["body"];
      for (const field of fields) {
        const raw = `trigger.${field}`;
        vars.push({
          value: mode === "jinja2" ? `{{ ${raw} }}` : raw,
          label: `trigger.${field}`,
          group: canvasTitle,
        });
      }
    } else {
      // Non-trigger nodes
      if (mode === "nodeId") {
        vars.push({
          value: node.id,
          label: `${node.id}  —  ${canvasTitle}`,
          group: "Nodes",
        });
        continue;
      }

      const outputFields = NODE_OUTPUT_FIELDS[label];

      if (outputFields === undefined) {
        // Unknown node type — show bare reference as a fallback
        vars.push({
          value: mode === "jinja2" ? `{{ ${node.id} }}` : node.id,
          label: `${node.id}  —  ${canvasTitle}`,
          group: `${node.id}  —  ${canvasTitle}`,
        });
      } else if (outputFields.length > 0) {
        // Known node with output fields
        for (const field of outputFields) {
          const raw = `${node.id}.${field}`;
          vars.push({
            value: mode === "jinja2" ? `{{ ${raw} }}` : raw,
            label: `${node.id}.${field}`,
            group: `${node.id}  —  ${canvasTitle}`,
          });
        }
        // else: known no-output node (Condition, Merge, ForEach) — omit entirely
      }
    }
  }

  return vars;
}

// ---------------------------------------------------------------------------
// Helpers for ExpressionInput
// ---------------------------------------------------------------------------

/**
 * Finds the "current token" the cursor is sitting on — everything after
 * the last word boundary (space, (, =, !, <, >, ,, ") up to the cursor.
 */
export function getCurrentToken(
  value: string,
  cursorPos: number,
): { token: string; start: number } {
  const before = value.substring(0, cursorPos);
  const match = /[\w._]*$/.exec(before);
  const token = match ? match[0] : "";
  return { token, start: cursorPos - token.length };
}

/**
 * Replaces the current token at cursorPos with the given suggestion string.
 * Returns the new full value and the new cursor position (end of insertion).
 */
export function insertAtCursor(
  value: string,
  cursorPos: number,
  suggestion: string,
): { newValue: string; newCursorPos: number } {
  const { start } = getCurrentToken(value, cursorPos);
  const newValue = value.substring(0, start) + suggestion + value.substring(cursorPos);
  return { newValue, newCursorPos: start + suggestion.length };
}
