import { beforeEach, describe, expect, it } from "vitest";
import {
  HISTORY_LIMIT,
  addToHistory,
  clearHistory,
  loadHistory,
  type PlaygroundHistoryEntry,
} from "./playgroundHistory";


const WF = "wf-xyz";

function entry(overrides: Partial<PlaygroundHistoryEntry> = {}): PlaygroundHistoryEntry {
  return {
    at: Date.now(),
    mode: "sync",
    status: "completed",
    elapsed_ms: 42,
    payload: "{}",
    instance_id: null,
    ...overrides,
  };
}


beforeEach(() => {
  window.localStorage.clear();
});


describe("loadHistory", () => {
  it("returns [] when no history is stored", () => {
    expect(loadHistory(WF)).toEqual([]);
  });

  it("returns [] when stored JSON is malformed", () => {
    window.localStorage.setItem(`aeai:playground:${WF}:history`, "not-json");
    expect(loadHistory(WF)).toEqual([]);
  });

  it("returns [] when stored value isn't an array", () => {
    window.localStorage.setItem(`aeai:playground:${WF}:history`, '"abc"');
    expect(loadHistory(WF)).toEqual([]);
  });

  it("drops entries that don't match the schema", () => {
    const mixed = [
      entry({ status: "ok1" }),
      { at: "not-a-number", mode: "sync", status: "x", payload: "{}" },
      null,
      entry({ status: "ok2", mode: "oops" as unknown as "sync" }),
    ];
    window.localStorage.setItem(`aeai:playground:${WF}:history`, JSON.stringify(mixed));
    const out = loadHistory(WF);
    expect(out).toHaveLength(1);
    expect(out[0].status).toBe("ok1");
  });

  it("is scoped per workflow id", () => {
    addToHistory(WF, entry({ status: "a" }));
    addToHistory("other-wf", entry({ status: "b" }));
    expect(loadHistory(WF).map((e) => e.status)).toEqual(["a"]);
    expect(loadHistory("other-wf").map((e) => e.status)).toEqual(["b"]);
  });
});


describe("addToHistory", () => {
  it("prepends the new entry so the freshest run is first", () => {
    addToHistory(WF, entry({ status: "old" }));
    addToHistory(WF, entry({ status: "newer" }));
    expect(loadHistory(WF).map((e) => e.status)).toEqual(["newer", "old"]);
  });

  it("caps the buffer at HISTORY_LIMIT", () => {
    for (let i = 0; i < HISTORY_LIMIT + 5; i++) {
      addToHistory(WF, entry({ status: `run-${i}` }));
    }
    const stored = loadHistory(WF);
    expect(stored).toHaveLength(HISTORY_LIMIT);
    // Newest entry is first.
    expect(stored[0].status).toBe(`run-${HISTORY_LIMIT + 4}`);
    // Oldest retained entry is the one from HISTORY_LIMIT iterations back.
    expect(stored[HISTORY_LIMIT - 1].status).toBe(`run-${5}`);
  });

  it("returns the new ring buffer so callers don't need a re-read", () => {
    const ret = addToHistory(WF, entry({ status: "first" }));
    expect(ret).toEqual(loadHistory(WF));
  });
});


describe("clearHistory", () => {
  it("removes the workflow's history only", () => {
    addToHistory(WF, entry({ status: "keep-gone" }));
    addToHistory("other-wf", entry({ status: "keep-this" }));
    clearHistory(WF);
    expect(loadHistory(WF)).toEqual([]);
    expect(loadHistory("other-wf")).toHaveLength(1);
  });
});
