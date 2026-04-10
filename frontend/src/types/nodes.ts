export type NodeCategory = "trigger" | "agent" | "action" | "logic";

export interface AgenticNodeData {
  [key: string]: unknown;
  /** Registry / engine label (must match node_registry.json). */
  label: string;
  /** Optional canvas title; defaults to `label` when empty. */
  displayName?: string;
  nodeCategory: NodeCategory;
  description?: string;
  config: Record<string, unknown>;
  status?: "idle" | "running" | "completed" | "failed" | "suspended";
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
