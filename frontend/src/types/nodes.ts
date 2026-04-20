export type NodeCategory = "trigger" | "agent" | "action" | "logic" | "knowledge" | "notification" | "nlp";

export interface AgenticNodeData {
  [key: string]: unknown;
  /** Registry / engine label (must match node_registry.json). */
  label: string;
  /** Optional canvas title; defaults to `label` when empty. */
  displayName?: string;
  nodeCategory: NodeCategory;
  description?: string;
  config: Record<string, unknown>;
  /**
   * Per-node live status overlay.
   *
   * ``idle``      — never executed in the current run (or reset).
   * ``running``   — log entry exists with status=running.
   * ``completed`` — log entry with status=completed.
   * ``failed``    — log entry with status=failed.
   * ``suspended`` — log entry with status=suspended (HITL or async-external).
   * ``paused``    — debug-replay only.
   * ``skipped``   — terminal run completed but this node was never
   *                 reached (Condition-branch-pruned or downstream of
   *                 a failure). Populated post-terminal by
   *                 ``computeNodeStatuses``.
   */
  status?:
    | "idle"
    | "running"
    | "completed"
    | "failed"
    | "suspended"
    | "paused"
    | "skipped";
}

/** Title shown on the node card and in expression picker groups. */
export function nodeCanvasTitle(d: AgenticNodeData): string {
  const raw = d.displayName;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  return d.label;
}

export interface PaletteItem {
  nodeCategory: NodeCategory;
  label: string;
  description: string;
  icon: string;
  defaultConfig: Record<string, unknown>;
}

import { REGISTRY_PALETTE } from "@/lib/registry";

export const NODE_PALETTE: PaletteItem[] = REGISTRY_PALETTE;
