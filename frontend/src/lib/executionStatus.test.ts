import { describe, expect, it } from "vitest";
import type { Node } from "@xyflow/react";

import {
  computeNodeStatuses,
  nodeStatusFromLog,
  shouldApplyTransition,
  statusForSingleLog,
  type LogLite,
  type NodeStatus,
} from "./executionStatus";

function node(id: string): Node {
  return {
    id,
    type: "agenticNode",
    position: { x: 0, y: 0 },
    data: { label: id, nodeCategory: "agent", config: {} },
  };
}

const N: Node[] = [node("trigger"), node("agent"), node("cond"), node("yes"), node("no")];

describe("nodeStatusFromLog", () => {
  it("maps known statuses", () => {
    expect(nodeStatusFromLog("running")).toBe("running");
    expect(nodeStatusFromLog("completed")).toBe("completed");
    expect(nodeStatusFromLog("failed")).toBe("failed");
    expect(nodeStatusFromLog("suspended")).toBe("suspended");
  });

  it("falls back to idle for unknown or empty values", () => {
    expect(nodeStatusFromLog("")).toBe("idle");
    expect(nodeStatusFromLog("banana")).toBe("idle");
  });
});


describe("shouldApplyTransition", () => {
  it("rejects no-op transitions", () => {
    expect(shouldApplyTransition("running", "running")).toBe(false);
  });

  it("allows progressing from idle to running", () => {
    expect(shouldApplyTransition("idle", "running")).toBe(true);
    expect(shouldApplyTransition(undefined, "running")).toBe(true);
  });

  it("allows running → terminal states", () => {
    expect(shouldApplyTransition("running", "completed")).toBe(true);
    expect(shouldApplyTransition("running", "failed")).toBe(true);
    expect(shouldApplyTransition("running", "suspended")).toBe(true);
  });

  it("rejects demoting a terminal node back to running or idle", () => {
    expect(shouldApplyTransition("completed", "running")).toBe(false);
    expect(shouldApplyTransition("completed", "idle")).toBe(false);
    expect(shouldApplyTransition("failed", "running")).toBe(false);
    expect(shouldApplyTransition("suspended", "running")).toBe(false);
  });

  it("allows transitions between terminal states (e.g. async resume)", () => {
    // Suspended-on-async-external → completed after Beat resumes.
    expect(shouldApplyTransition("suspended", "completed")).toBe(true);
    // Failed → suspended shouldn't happen in practice but don't block it.
    expect(shouldApplyTransition("failed", "suspended")).toBe(true);
  });
});


describe("computeNodeStatuses — mid-run", () => {
  it("all nodes idle when no logs", () => {
    const s = computeNodeStatuses(N, [], "running");
    expect(Object.values(s)).toEqual(["idle", "idle", "idle", "idle", "idle"]);
  });

  it("nodes without logs stay idle while instance is running", () => {
    const logs: LogLite[] = [
      { node_id: "trigger", status: "completed" },
      { node_id: "agent", status: "running" },
    ];
    const s = computeNodeStatuses(N, logs, "running");
    expect(s).toEqual({
      trigger: "completed",
      agent: "running",
      cond: "idle",
      yes: "idle",
      no: "idle",
    });
  });

  it("later log for the same node upgrades earlier status", () => {
    const logs: LogLite[] = [
      { node_id: "agent", status: "running" },
      { node_id: "agent", status: "completed" },
    ];
    const s = computeNodeStatuses(N, logs, "running");
    expect(s.agent).toBe("completed");
  });

  it("out-of-order late 'running' log does not demote a completed node", () => {
    const logs: LogLite[] = [
      { node_id: "agent", status: "completed" },
      { node_id: "agent", status: "running" }, // stale — delayed by the stream
    ];
    const s = computeNodeStatuses(N, logs, "running");
    expect(s.agent).toBe("completed");
  });
});


describe("computeNodeStatuses — terminal run", () => {
  it("marks unreached nodes skipped on terminal", () => {
    const logs: LogLite[] = [
      { node_id: "trigger", status: "completed" },
      { node_id: "agent", status: "completed" },
      { node_id: "cond", status: "completed" },
      { node_id: "yes", status: "completed" }, // the 'true' branch fired
      // 'no' branch was pruned → never got a log
    ];
    const s = computeNodeStatuses(N, logs, "completed");
    expect(s.no).toBe("skipped");
    expect(s.yes).toBe("completed");
  });

  it("marks downstream nodes skipped when an earlier node failed", () => {
    const logs: LogLite[] = [
      { node_id: "trigger", status: "completed" },
      { node_id: "agent", status: "failed" },
    ];
    const s = computeNodeStatuses(N, logs, "failed");
    expect(s.agent).toBe("failed");
    expect(s.cond).toBe("skipped");
    expect(s.yes).toBe("skipped");
    expect(s.no).toBe("skipped");
  });

  it("cancelled is a terminal status too", () => {
    const logs: LogLite[] = [{ node_id: "trigger", status: "completed" }];
    const s = computeNodeStatuses(N, logs, "cancelled");
    expect(s.agent).toBe("skipped");
  });

  it("timed_out is a terminal status too", () => {
    const logs: LogLite[] = [{ node_id: "trigger", status: "completed" }];
    const s = computeNodeStatuses(N, logs, "timed_out");
    expect(s.agent).toBe("skipped");
  });
});


describe("computeNodeStatuses — suspended variants", () => {
  it("HITL-suspended keeps downstream as idle (transient pause)", () => {
    const logs: LogLite[] = [
      { node_id: "trigger", status: "completed" },
      { node_id: "agent", status: "suspended" },
    ];
    const s = computeNodeStatuses(N, logs, "suspended");
    expect(s.agent).toBe("suspended");
    // Downstream nodes are NOT marked skipped — the workflow can still
    // resume and run them.
    expect(s.cond).toBe("idle");
    expect(s.yes).toBe("idle");
    expect(s.no).toBe("idle");
  });

  it("async-external-suspended has the same non-terminal treatment", () => {
    // Instance status stays "suspended"; suspended_reason is on the
    // instance, not in logs. Same idle-downstream behaviour.
    const logs: LogLite[] = [
      { node_id: "trigger", status: "completed" },
      { node_id: "agent", status: "suspended" },
    ];
    const s = computeNodeStatuses(N, logs, "suspended");
    expect(s.cond).toBe("idle");
  });
});


describe("computeNodeStatuses — edge cases", () => {
  it("logs referencing a removed node are ignored", () => {
    const logs: LogLite[] = [{ node_id: "ghost", status: "completed" }];
    const s = computeNodeStatuses(N, logs, "running");
    expect("ghost" in s).toBe(false);
  });

  it("returns an empty map for an empty graph", () => {
    expect(computeNodeStatuses([], [], "running")).toEqual({});
  });

  it("single-node graph with log", () => {
    const one = [node("only")];
    const s = computeNodeStatuses(one, [{ node_id: "only", status: "completed" }], "completed");
    expect(s).toEqual({ only: "completed" });
  });
});


describe("statusForSingleLog", () => {
  it("returns the new status when the transition is allowed", () => {
    expect(statusForSingleLog("idle", { node_id: "x", status: "running" })).toBe("running");
    expect(statusForSingleLog("running", { node_id: "x", status: "completed" })).toBe("completed");
  });

  it("returns null for no-op transitions", () => {
    expect(statusForSingleLog("completed", { node_id: "x", status: "completed" })).toBeNull();
  });

  it("returns null when the transition is blocked", () => {
    expect(statusForSingleLog("completed", { node_id: "x", status: "running" })).toBeNull();
  });

  it("handles undefined prior (first log for a node)", () => {
    expect(statusForSingleLog(undefined, { node_id: "x", status: "running" })).toBe("running");
  });
});


describe("computeNodeStatuses — ForEach-like multi-entry", () => {
  it("does not oscillate between running and completed across iterations", () => {
    // Hypothetical ForEach: the body node runs twice, each iteration
    // produces its own running + completed pair. The final status should
    // still settle on completed.
    const logs: LogLite[] = [
      { node_id: "agent", status: "running" },
      { node_id: "agent", status: "completed" },
      { node_id: "agent", status: "running" }, // iteration 2
      { node_id: "agent", status: "completed" }, // iteration 2
    ];
    const s = computeNodeStatuses(N, logs, "completed");
    expect(s.agent).toBe("completed");
  });

  it("failed iteration in ForEach leaves node failed", () => {
    const logs: LogLite[] = [
      { node_id: "agent", status: "running" },
      { node_id: "agent", status: "completed" },
      { node_id: "agent", status: "running" },
      { node_id: "agent", status: "failed" },
    ];
    const s = computeNodeStatuses(N, logs, "failed");
    expect(s.agent).toBe("failed");
  });
});


// Type assertion — confirms `NodeStatus` exports the full canvas union.
const _typeCheck: NodeStatus[] = [
  "idle", "running", "completed", "failed", "suspended", "paused", "skipped",
];
void _typeCheck;
