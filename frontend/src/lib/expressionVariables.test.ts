import { describe, it, expect } from "vitest";
import { getCurrentToken, insertAtCursor } from "./expressionVariables";

describe("getCurrentToken", () => {
  it("returns empty token at start", () => {
    const { token, start } = getCurrentToken("", 0);
    expect(token).toBe("");
    expect(start).toBe(0);
  });

  it("picks up a simple identifier", () => {
    const { token, start } = getCurrentToken("node_2", 6);
    expect(token).toBe("node_2");
    expect(start).toBe(0);
  });

  it("treats dots and underscores as part of the token", () => {
    const { token, start } = getCurrentToken("node_2.intent", 13);
    expect(token).toBe("node_2.intent");
    expect(start).toBe(0);
  });

  it("breaks on a space boundary", () => {
    const { token, start } = getCurrentToken("node_2.intent == nod", 20);
    expect(token).toBe("nod");
    expect(start).toBe(17);
  });

  it("breaks on a quote boundary", () => {
    const { token, start } = getCurrentToken('node_2.intent == "diagno', 24);
    expect(token).toBe("diagno");
    expect(start).toBe(18);
  });

  it("handles cursor in middle of identifier", () => {
    const { token, start } = getCurrentToken("foo.bar baz", 3);
    expect(token).toBe("foo");
    expect(start).toBe(0);
  });

  it("handles operator characters as boundaries", () => {
    for (const op of ["(", "=", "!", "<", ">", ","]) {
      const text = `a ${op}b`;
      const { token } = getCurrentToken(text, text.length);
      expect(token).toBe("b");
    }
  });
});

describe("insertAtCursor", () => {
  it("replaces the current token with the suggestion", () => {
    const { newValue, newCursorPos } = insertAtCursor("nod", 3, "node_2.intent");
    expect(newValue).toBe("node_2.intent");
    expect(newCursorPos).toBe(13);
  });

  it("leaves text after the cursor intact", () => {
    const { newValue, newCursorPos } = insertAtCursor(
      'nod == "foo"',
      3,
      "node_2.intent",
    );
    expect(newValue).toBe('node_2.intent == "foo"');
    expect(newCursorPos).toBe(13);
  });

  it("inserts at an empty token position", () => {
    const { newValue, newCursorPos } = insertAtCursor("a == ", 5, "node_1");
    expect(newValue).toBe("a == node_1");
    expect(newCursorPos).toBe(11);
  });
});
