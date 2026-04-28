/**
 * COPILOT-02.i — smoke tests for the event renderer.
 *
 * Full end-to-end chat flow has too many moving parts (SSE fetch,
 * streaming async iterator, workflow store, session bootstrap) for
 * a useful component-unit test. We instead assert the event
 * dispatch + result-summary strings — these are the bits users read
 * at a glance and that are easy to regress when adding a new tool.
 */

import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import type { CopilotAgentEvent } from "@/lib/api";
import { CopilotEventCard } from "./CopilotToolResultCard";


describe("CopilotEventCard", () => {
  // Vitest's default config doesn't register testing-library's
  // auto-cleanup, so DOM from a previous `render` leaks into the
  // next test. Two tests that both render a tool_result for
  // "check_draft" would produce two "Expand tool result for
  // check_draft" buttons without explicit cleanup — hence this.
  afterEach(cleanup);

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

  // SMART-04 — lint rendering.
  it("surfaces lint counts on the collapsed tool_result summary", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "check_draft",
      result: {
        errors: [],
        warnings: [],
        lints: [
          { code: "no_trigger", severity: "error", message: "no trigger",
            fix_hint: "add one", node_id: null },
          { code: "disconnected_node", severity: "warn", message: "floater",
            fix_hint: null, node_id: "node_3" },
        ],
        lints_enabled: true,
      },
      validation: {
        errors: [],
        warnings: [],
        lints: [
          { code: "no_trigger", severity: "error", message: "no trigger",
            fix_hint: "add one", node_id: null },
          { code: "disconnected_node", severity: "warn", message: "floater",
            fix_hint: null, node_id: "node_3" },
        ],
        lints_enabled: true,
      },
      draft_version: 2,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    // Counts visible on the collapsed summary row.
    expect(screen.getByText(/1 lint error/i)).toBeInTheDocument();
    expect(screen.getByText(/1 lint warning/i)).toBeInTheDocument();
  });

  it("expands lint cards with severity, code, node_id, message, and fix_hint", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "check_draft",
      result: {
        errors: [],
        warnings: [],
        lints: [
          {
            code: "missing_credential",
            severity: "error",
            message: "Node node_2 uses provider google but no key is set.",
            fix_hint: "Open the LLM Credentials dialog.",
            node_id: "node_2",
          },
        ],
        lints_enabled: true,
      },
      validation: {
        errors: [],
        warnings: [],
        lints: [
          {
            code: "missing_credential",
            severity: "error",
            message: "Node node_2 uses provider google but no key is set.",
            fix_hint: "Open the LLM Credentials dialog.",
            node_id: "node_2",
          },
        ],
        lints_enabled: true,
      },
      draft_version: 1,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    // Expand the tool_result card.
    const toggle = screen.getByRole("button", {
      name: /Expand tool result for check_draft/i,
    });
    fireEvent.click(toggle);
    expect(screen.getByText(/Lints \(SMART-04\)/i)).toBeInTheDocument();
    // Lint fields appear in the lint-card AND in the raw JSON pre
    // when expanded — getAllByText + length check is the stable way
    // to assert "the lint card rendered" without being fragile to
    // the JSON dump also containing the same strings.
    expect(screen.getAllByText(/missing_credential/).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Node node_2 uses provider google but no key is set\./i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Open the LLM Credentials dialog\./i).length,
    ).toBeGreaterThan(0);
  });

  it("renders recall_patterns summary with match count and disabled state", () => {
    const enabled: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "recall_patterns",
      result: {
        enabled: true,
        query: "summarise slack",
        match_count: 3,
        patterns: [],
      },
      validation: null,
      draft_version: 1,
      error: null,
    };
    const { unmount } = render(<CopilotEventCard event={enabled} />);
    expect(screen.getByText(/3 patterns/)).toBeInTheDocument();
    unmount();

    const disabled: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "recall_patterns",
      result: { enabled: false, query: "x", match_count: 0, patterns: [] },
      validation: null,
      draft_version: 1,
      error: null,
    };
    render(<CopilotEventCard event={disabled} />);
    expect(screen.getByText("disabled")).toBeInTheDocument();
  });

  it("renders discover_mcp_tools summary with tool count and disabled state", () => {
    const enabled: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "discover_mcp_tools",
      result: {
        discovery_enabled: true,
        server_label: null,
        tools: [{ name: "enrich_ip" }, { name: "create_ticket" }],
      },
      validation: null,
      draft_version: 1,
      error: null,
    };
    const { unmount } = render(<CopilotEventCard event={enabled} />);
    expect(screen.getByText(/2 MCP tools/)).toBeInTheDocument();
    unmount();

    const disabled: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "discover_mcp_tools",
      result: { discovery_enabled: false, server_label: null, tools: [] },
      validation: null,
      draft_version: 1,
      error: null,
    };
    render(<CopilotEventCard event={disabled} />);
    expect(screen.getByText("disabled")).toBeInTheDocument();
  });

  it("shows a lints-disabled hint when tenant opted out", () => {
    const event: CopilotAgentEvent = {
      type: "tool_result",
      id: "toolu_1",
      name: "check_draft",
      result: { errors: [], warnings: [], lints: [], lints_enabled: false },
      validation: {
        errors: [],
        warnings: [],
        lints: [],
        lints_enabled: false,
      },
      draft_version: 1,
      error: null,
    };
    render(<CopilotEventCard event={event} />);
    const toggle = screen.getByRole("button", {
      name: /Expand tool result for check_draft/i,
    });
    fireEvent.click(toggle);
    expect(screen.getByText(/lints disabled per tenant policy/i)).toBeInTheDocument();
  });
});
