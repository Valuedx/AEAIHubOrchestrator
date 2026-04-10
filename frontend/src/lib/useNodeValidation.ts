/**
 * useNodeValidation
 *
 * Reactively runs validateWorkflow() whenever the canvas nodes or edges change.
 * Returns two sets of node IDs so AgenticNode can apply visual indicators
 * without re-running the full validation inside every node component.
 *
 * Usage:
 *   const { errorIds, warningIds } = useNodeValidation();
 *   const hasError = errorIds.has(nodeId);
 */

import { useMemo } from "react";
import { useFlowStore } from "@/store/flowStore";
import { validateWorkflow } from "@/lib/validateWorkflow";

interface NodeValidationResult {
  /** Node IDs that have at least one hard error (blocks execution) */
  errorIds: Set<string>;
  /** Node IDs that have at least one warning (e.g. disconnected) */
  warningIds: Set<string>;
}

export function useNodeValidation(): NodeValidationResult {
  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);

  return useMemo(() => {
    const errors = validateWorkflow(nodes, edges);
    const errorIds = new Set<string>();
    const warningIds = new Set<string>();

    for (const e of errors) {
      if (!e.nodeId) continue; // graph-level errors have no node ID
      if (e.severity === "error") errorIds.add(e.nodeId);
      else warningIds.add(e.nodeId);
    }

    return { errorIds, warningIds };
  }, [nodes, edges]);
}
