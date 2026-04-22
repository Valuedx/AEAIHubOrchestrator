/**
 * COPILOT-02.i — smoke tests for the event renderer.
 *
 * Full end-to-end chat flow has too many moving parts (SSE fetch,
 * streaming async iterator, workflow store, session bootstrap) for
 * a useful component-unit test. We instead assert the event
 * dispatch + result-summary strings — these are the bits users read
 * at a glance and that are easy to regress when adding a new tool.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import type { CopilotAgentEvent } from "@/lib/api";
import { CopilotEventCard } from "./CopilotToolResultCard";


describe("CopilotEventCard", () => {
  it("renders assistant text", () => {
    const event: CopilotAgentEvent = {
      type: "assistant_text",
      text: "Hello from the system.",
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText("Hello from the system.")).toBeInTheDocument();
    // Role label is the uppercase "Copilot" span above the bubble.
    expect(screen.getByText("Copilot")).toBeInTheDocument();
  });

  it("renders done event as null (no DOM noise)", () => {
    const event: CopilotAgentEvent = {
      type: "done",
      turns_added: [],
      final_text: "",
    };
    const { container } = render(<CopilotEventCard event={event} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders recoverable errors in an amber banner", () => {
    const event: CopilotAgentEvent = {
      type: "error",
      message: "Agent hit iteration cap",
      recoverable: true,
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText(/Recoverable error/i)).toBeInTheDocument();
    expect(screen.getByText(/iteration cap/i)).toBeInTheDocument();
  });

  it("renders fatal errors with the 'Agent error' headline", () => {
    const event: CopilotAgentEvent = {
      type: "error",
      message: "LLM call failed: timeout",
      recoverable: false,
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText(/Agent error/i)).toBeInTheDocument();
  });

  it("renders a tool_call pill with a readable arg summary", () => {
    const event: CopilotAgentEvent = {
      type: "tool_call",
      id: "toolu_1",
      name: "add_node",
      args: { node_type: "llm_agent" },
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText("add_node")).toBeInTheDocument();
    expect(screen.getByText(/llm_agent/)).toBeInTheDocument();
  });

  it("expands a tool_result to show result JSON on click", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "add_node",
      result: { node_id: "node_xyz", node: { id: "node_xyz" } },
      validation: { errors: [], warnings: [] },
      draft_version: 3,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    // Summary visible without expanding.
    expect(screen.getByText(/added node_xyz/i)).toBeInTheDocument();
    // Click to expand via the aria-labelled header.
    const toggleButton = screen.getByRole("button", {
      name: /Expand tool result for add_node/i,
    });
    fireEvent.click(toggleButton);
    expect(screen.getByText("Result")).toBeInTheDocument();
    expect(screen.getByText("draft v3")).toBeInTheDocument();
  });

  it("highlights tool_result errors in red + surfaces the error message", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "add_node",
      result: { error: "Unknown foo_type" },
      validation: null,
      draft_version: 1,
      error: "Unknown foo_type",
    };
    render(<CopilotEventCard event={event} />);
    // "failed" is unique to the error-state summary span.
    expect(screen.getByText("failed")).toBeInTheDocument();
    // The error string renders as a paragraph under the tool name.
    expect(screen.getByText("Unknown foo_type")).toBeInTheDocument();
  });

  it("renders validate_graph summary with error + warning counts", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "validate_graph",
      result: { errors: [], warnings: ["Node node_1: temperature above max"] },
      validation: null,
      draft_version: 2,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText(/0 errors · 1 warnings/)).toBeInTheDocument();
  });

  it("renders execute_draft summary with status + elapsed_ms", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "execute_draft",
      result: { instance_id: "xyz", status: "completed", elapsed_ms: 412 },
      validation: null,
      draft_version: 2,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText(/completed/)).toBeInTheDocument();
    expect(screen.getByText(/412 ms/)).toBeInTheDocument();
  });

  it("renders search_docs summary with match count", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "search_docs",
      result: { query: "intent classifier", match_count: 7, results: [] },
      validation: null,
      draft_version: 1,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    expect(screen.getByText(/7 matches/)).toBeInTheDocument();
  });
});
