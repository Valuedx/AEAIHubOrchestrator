/**
 * COPILOT-02.ii — unit tests for ``turnsToChatItems``.
 *
 * The replay helper has to translate three persistence shapes
 * (user / assistant / tool turns) into the ``ChatItem`` shape the
 * message list renders. Breaking the mapping silently would show
 * empty bubbles or missing tool-result cards on reopen — these
 * tests pin the shape.
 */

import { describe, it, expect } from "vitest";
import type { CopilotTurnOut } from "@/lib/api";
import { turnsToChatItems } from "./CopilotPanel";


function turn(overrides: Partial<CopilotTurnOut> & { id: string; role: string }): CopilotTurnOut {
  return {
    session_id: "sess-1",
    turn_index: 0,
    content_json: {},
    tool_calls_json: null,
    token_usage_json: null,
    created_at: "2026-04-21T00:00:00Z",
    ...overrides,
  };
}


describe("turnsToChatItems", () => {
  it("maps a user turn to a user bubble", () => {
    const items = turnsToChatItems([
      turn({ id: "t1", role: "user", content_json: { text: "hi" } }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe("user");
    expect(items[0].userText).toBe("hi");
  });

  it("maps an assistant text turn to a single assistant_text event", () => {
    const items = turnsToChatItems([
      turn({
        id: "t2",
        role: "assistant",
        content_json: { text: "Here's what I'll do...", blocks: [] },
      }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe("event");
    expect(items[0].event?.type).toBe("assistant_text");
    if (items[0].event?.type === "assistant_text") {
      expect(items[0].event.text).toBe("Here's what I'll do...");
    }
  });

  it("fans out assistant turns with tool_calls into text + tool_call events", () => {
    const items = turnsToChatItems([
      turn({
        id: "t3",
        role: "assistant",
        content_json: { text: "Adding the node.", blocks: [] },
        tool_calls_json: [
          { id: "tu-1", name: "add_node", input: { node_type: "http" } },
        ],
      }),
    ]);
    expect(items).toHaveLength(2);
    expect(items[0].event?.type).toBe("assistant_text");
    expect(items[1].event?.type).toBe("tool_call");
    if (items[1].event?.type === "tool_call") {
      expect(items[1].event.id).toBe("tu-1");
      expect(items[1].event.name).toBe("add_node");
      expect(items[1].event.args).toEqual({ node_type: "http" });
    }
  });

  it("maps a tool turn to a tool_result event with sentinel validation/version", () => {
    const items = turnsToChatItems([
      turn({
        id: "t4",
        role: "tool",
        content_json: {
          tool_use_id: "tu-1",
          name: "add_node",
          args: { node_type: "http" },
          result: { node_id: "node_1" },
        },
      }),
    ]);
    expect(items).toHaveLength(1);
    const ev = items[0].event;
    expect(ev?.type).toBe("tool_result");
    if (ev?.type === "tool_result") {
      expect(ev.id).toBe("tu-1");
      expect(ev.name).toBe("add_node");
      expect(ev.result).toEqual({ node_id: "node_1" });
      // Replay cannot reconstruct per-turn validation / draft_version.
      expect(ev.validation).toBeNull();
      expect(ev.draft_version).toBe(0);
      expect(ev.error).toBeNull();
    }
  });

  it("carries through tool errors", () => {
    const items = turnsToChatItems([
      turn({
        id: "t5",
        role: "tool",
        content_json: {
          tool_use_id: "tu-2",
          name: "connect_nodes",
          result: {},
          error: "Missing source node",
        },
      }),
    ]);
    const ev = items[0].event;
    if (ev?.type === "tool_result") {
      expect(ev.error).toBe("Missing source node");
    } else {
      throw new Error("expected tool_result");
    }
  });

  it("preserves chronological order across mixed turns", () => {
    const items = turnsToChatItems([
      turn({ id: "a", role: "user", content_json: { text: "do the thing" } }),
      turn({
        id: "b",
        role: "assistant",
        content_json: { text: "okay", blocks: [] },
        tool_calls_json: [{ id: "tu-1", name: "add_node", input: {} }],
      }),
      turn({
        id: "c",
        role: "tool",
        content_json: { tool_use_id: "tu-1", name: "add_node", result: {} },
      }),
    ]);
    expect(items.map((i) => i.kind)).toEqual(["user", "event", "event", "event"]);
    expect(items[1].event?.type).toBe("assistant_text");
    expect(items[2].event?.type).toBe("tool_call");
    expect(items[3].event?.type).toBe("tool_result");
  });
});
