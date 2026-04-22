/**
 * COPILOT-02.ii — PromoteDialog smoke tests.
 *
 * The dialog is the last checkpoint before a draft lands as a
 * workflow definition, so we guard the three UX invariants that
 * users rely on:
 *
 *   1. Net-new drafts require a workflow name before promote is
 *      allowed; forks don't (name is inherited from the base).
 *   2. Promote is blocked while the draft has validation errors
 *      or lint errors — warnings don't block.
 *   3. On confirm, we call ``api.promoteDraft`` with the right
 *      body shape and forward the response to ``onPromoted``.
 */

import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import type { CopilotDraftOut, CopilotPromoteOut } from "@/lib/api";
import { api } from "@/lib/api";
import { PromoteDialog } from "./PromoteDialog";


function makeDraft(overrides: Partial<CopilotDraftOut> = {}): CopilotDraftOut {
  return {
    id: "draft-1",
    tenant_id: "t-1",
    title: "Edits on \"Slack summariser\"",
    base_workflow_id: "wf-1",
    base_version_at_fork: 3,
    graph_json: { nodes: [{}, {}, {}], edges: [{}, {}] },
    version: 2,
    created_by: null,
    created_at: "2026-04-21T00:00:00Z",
    updated_at: "2026-04-21T00:00:00Z",
    validation: { errors: [], warnings: [], lints: [] },
    ...overrides,
  };
}


describe("PromoteDialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("blocks promote while the draft has validation errors", () => {
    const draft = makeDraft({
      validation: {
        errors: ["Start node has no outgoing edge"],
        warnings: [],
        lints: [],
      },
    });

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={draft}
        baseWorkflowName="Slack summariser"
        baseNodeCount={2}
        baseEdgeCount={1}
        onPromoted={vi.fn()}
      />,
    );

    const confirmBtn = screen.getByRole("button", {
      name: /save new version/i,
    });
    expect(confirmBtn).toBeDisabled();
    expect(screen.getByText(/no outgoing edge/)).toBeInTheDocument();
  });

  it("allows promote when validation has only warnings", () => {
    const draft = makeDraft({
      validation: {
        errors: [],
        warnings: ["Node node_3 uses a deprecated handler"],
        lints: [
          {
            code: "LINT_NAMING",
            severity: "warn",
            node_id: null,
            message: "Workflow name looks terse",
          },
        ],
      },
    });

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={draft}
        baseWorkflowName="Slack summariser"
        onPromoted={vi.fn()}
      />,
    );

    const confirmBtn = screen.getByRole("button", {
      name: /save new version/i,
    });
    expect(confirmBtn).not.toBeDisabled();
  });

  it("requires a workflow name for net-new drafts", () => {
    const draft = makeDraft({
      base_workflow_id: null,
      base_version_at_fork: 0,
      title: "New workflow draft",
    });

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={draft}
        baseWorkflowName={null}
        onPromoted={vi.fn()}
      />,
    );

    const nameInput = screen.getByLabelText(/Workflow name/i);
    // The title "New workflow draft" is prefilled, so the button
    // starts enabled. Clearing the input must disable it again.
    const confirmBtn = screen.getByRole("button", {
      name: /create workflow/i,
    });
    expect(confirmBtn).not.toBeDisabled();
    fireEvent.change(nameInput, { target: { value: "   " } });
    expect(confirmBtn).toBeDisabled();
    fireEvent.change(nameInput, { target: { value: "Slack summariser" } });
    expect(confirmBtn).not.toBeDisabled();
  });

  it("renders +/- deltas against the base workflow counts", () => {
    const draft = makeDraft({
      graph_json: { nodes: [{}, {}, {}, {}], edges: [{}, {}] },
    });

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={draft}
        baseWorkflowName="Slack summariser"
        baseNodeCount={2}
        baseEdgeCount={2}
        onPromoted={vi.fn()}
      />,
    );

    // +2 nodes, 0 edge delta (not rendered when zero).
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("calls api.promoteDraft and forwards result on confirm", async () => {
    const draft = makeDraft({ version: 5 });
    const promoted: CopilotPromoteOut = {
      workflow_id: "wf-1",
      version: 4,
      created: false,
    };
    const spy = vi
      .spyOn(api, "promoteDraft")
      .mockResolvedValueOnce(promoted);
    const onPromoted = vi.fn();

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={draft}
        baseWorkflowName="Slack summariser"
        onPromoted={onPromoted}
      />,
    );

    const confirmBtn = screen.getByRole("button", {
      name: /save new version/i,
    });
    fireEvent.click(confirmBtn);

    // Wait a microtask for the async handler to run.
    await Promise.resolve();
    await Promise.resolve();

    expect(spy).toHaveBeenCalledWith("draft-1", { expected_version: 5 });
    expect(onPromoted).toHaveBeenCalledWith(promoted);
  });
});
