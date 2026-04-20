/**
 * Pure helpers that map workflow execution state onto per-node status
 * values for the canvas + Flow view.
 *
 * Separated from the Zustand store so the reducer logic is unit-testable
 * without mounting React or calling the API. The store consumes these
 * helpers from its SSE handlers.
 */

import type { Node } from "@xyflow/react";
import type { AgenticNodeData } from "@/types/nodes";

/** Subset of ExecutionLogOut that drives status inference. */
export interface LogLite {
  node_id: string;
  status: string; // "running" | "completed" | "failed" | "suspended" | ...
}

export type NodeStatus = NonNullable<AgenticNodeData["status"]>;

/** Instance statuses that mean "no more nodes will fire on this run". */
const TERMINAL_INSTANCE_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
]);

/** Map a log.status to a node-level status. Unknown values fall through
 *  to ``idle`` so a weird event doesn't crash the UI. */
export function nodeStatusFromLog(logStatus: string): NodeStatus {
  switch (logStatus) {
    case "running":
      return "running";
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    case "suspended":
      return "suspended";
    default:
      return "idle";
  }
}

/** Return true if a transition from ``prev`` → ``next`` should be applied.
 *
 *  Once a node is terminal in this run (completed/failed/suspended) we
 *  don't demote it back to running — that would flicker if a late log
 *  event arrives out of order.
 */
export function shouldApplyTransition(
  prev: NodeStatus | undefined,
  next: NodeStatus,
): boolean {
  if (prev === next) return false;
  const prevIsTerminal =
    prev === "completed" || prev === "failed" || prev === "suspended";
  // Allow any transition INTO a terminal state (e.g. running → completed).
  const nextIsTerminal =
    next === "completed" || next === "failed" || next === "suspended";
  if (prevIsTerminal && !nextIsTerminal) return false;
  return true;
}

/**
 * Compute the full per-node status map for a workflow given its graph,
 * the execution logs collected so far, and the current instance status.
 *
 * Rules:
 *  * Every node starts at ``idle``.
 *  * Nodes with a matching log entry take that log's status (via
 *    ``nodeStatusFromLog``). When a node has MULTIPLE log entries (rare
 *    but possible on ForEach iterations), the latest non-idle status wins.
 *  * If the instance is in a terminal state, any node that never got a
 *    log entry is marked ``skipped`` — covers both Condition-branch-
 *    pruned and never-reached-due-to-earlier-failure cases. For v1 the
 *    two are indistinguishable in the UI; an explanatory tooltip can
 *    be layered on top later without changing this helper.
 *  * Mid-run (instance still running / queued / suspended), unreached
 *    nodes stay ``idle``.
 */
export function computeNodeStatuses(
  nodes: Node[],
  logs: readonly LogLite[],
  instanceStatus: string,
): Record<string, NodeStatus> {
  // DV-03 — stickies are annotations; they never produce logs and should
  // not be marked ``skipped`` when the instance terminates.
  const executable = nodes.filter((n) => n.type !== "stickyNote");
  const out: Record<string, NodeStatus> = {};
  for (const n of executable) {
    out[n.id] = "idle";
  }

  // Fold logs — later entries can upgrade earlier ones only via the
  // ``shouldApplyTransition`` guard.
  for (const log of logs) {
    if (!(log.node_id in out)) continue; // log references a node no longer in the graph
    const next = nodeStatusFromLog(log.status);
    if (shouldApplyTransition(out[log.node_id], next)) {
      out[log.node_id] = next;
    }
  }

  if (TERMINAL_INSTANCE_STATUSES.has(instanceStatus)) {
    const reached = new Set(logs.map((l) => l.node_id));
    for (const n of executable) {
      if (!reached.has(n.id) && out[n.id] === "idle") {
        out[n.id] = "skipped";
      }
    }
  }

  return out;
}

/**
 * Incremental single-log update. Returns ``null`` when no change should
 * be applied (matches the shouldApplyTransition guard). The store calls
 * this on every SSE ``log`` event so we don't recompute the whole map
 * for a one-node change.
 */
export function statusForSingleLog(
  prev: NodeStatus | undefined,
  log: LogLite,
): NodeStatus | null {
  const next = nodeStatusFromLog(log.status);
  return shouldApplyTransition(prev, next) ? next : null;
}
