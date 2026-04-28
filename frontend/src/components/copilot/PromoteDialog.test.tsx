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

import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import type {
  CopilotDraftOut,
  CopilotPromoteOut,
  CopilotScenarioOut,
  CopilotScenariosRunAllOut,
} from "@/lib/api";
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
  beforeEach(() => {
    // Default: no saved scenarios. Tests that need scenarios
    // override this with a specific mockResolvedValue.
    vi.spyOn(api, "listDraftScenarios").mockResolvedValue([]);
  });

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
            fix_hint: null,
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

  // -------------------------------------------------------------------------
  // COPILOT-03.e — scenarios section + gate
  // -------------------------------------------------------------------------

  it("hides the scenarios section when no scenarios are saved", async () => {
    const draft = makeDraft();
    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={draft}
        baseWorkflowName="Slack summariser"
        onPromoted={vi.fn()}
      />,
    );
    // Let the listDraftScenarios promise resolve.
    await Promise.resolve();
    expect(screen.queryByText(/Saved scenarios/i)).not.toBeInTheDocument();
  });

  it("renders saved scenarios with a Run all button", async () => {
    const scenarios: CopilotScenarioOut[] = [
      {
        scenario_id: "s-1",
        name: "empty payload",
        payload: {},
        has_expected: true,
        created_at: "2026-04-20T00:00:00Z",
      },
      {
        scenario_id: "s-2",
        name: "oversized attachment",
        payload: {},
        has_expected: false,
        created_at: "2026-04-21T00:00:00Z",
      },
    ];
    vi.spyOn(api, "listDraftScenarios").mockResolvedValue(scenarios);

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={makeDraft()}
        baseWorkflowName="Slack summariser"
        onPromoted={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText(/Saved scenarios \(2\)/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("empty payload")).toBeInTheDocument();
    expect(screen.getByText("oversized attachment")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run all scenarios/i })).toBeInTheDocument();
  });

  it("blocks promote after failed scenarios until 'promote anyway' is checked", async () => {
    const scenarios: CopilotScenarioOut[] = [
      {
        scenario_id: "s-1",
        name: "empty payload",
        payload: {},
        has_expected: true,
        created_at: "2026-04-20T00:00:00Z",
      },
    ];
    const runResult: CopilotScenariosRunAllOut = {
      count: 1,
      pass_count: 0,
      fail_count: 1,
      stale_count: 0,
      error_count: 0,
      results: [
        {
          scenario_id: "s-1",
          name: "empty payload",
          status: "fail",
          mismatches: [{ path: "$.status", expected: "ok", actual: "failed" }],
          actual_output: null,
          message: null,
        },
      ],
    };
    vi.spyOn(api, "listDraftScenarios").mockResolvedValue(scenarios);
    vi.spyOn(api, "runAllDraftScenarios").mockResolvedValue(runResult);

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={makeDraft()}
        baseWorkflowName="Slack summariser"
        onPromoted={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("empty payload")).toBeInTheDocument(),
    );

    const runBtn = screen.getByRole("button", { name: /run all scenarios/i });
    fireEvent.click(runBtn);
    await waitFor(() => expect(screen.getAllByText("fail").length).toBeGreaterThan(0));

    // Apply is disabled until user confirms.
    const confirmBtn = screen.getByRole("button", { name: /save new version/i });
    expect(confirmBtn).toBeDisabled();

    // Checking the "promote anyway" box re-enables it.
    const confirmCheckbox = screen.getByRole("checkbox");
    fireEvent.click(confirmCheckbox);
    expect(confirmBtn).not.toBeDisabled();
  });

  it("leaves Apply enabled when all scenarios pass", async () => {
    const scenarios: CopilotScenarioOut[] = [
      {
        scenario_id: "s-1", name: "a", payload: {},
        has_expected: true, created_at: "2026-04-20T00:00:00Z",
      },
    ];
    vi.spyOn(api, "listDraftScenarios").mockResolvedValue(scenarios);
    vi.spyOn(api, "runAllDraftScenarios").mockResolvedValue({
      count: 1, pass_count: 1, fail_count: 0, stale_count: 0, error_count: 0,
      results: [
        { scenario_id: "s-1", name: "a", status: "pass", mismatches: [], actual_output: null, message: null },
      ],
    });

    render(
      <PromoteDialog
        open={true}
        onClose={vi.fn()}
        draft={makeDraft()}
        baseWorkflowName="Slack summariser"
        onPromoted={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("a")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /run all scenarios/i }));
    await waitFor(() => expect(screen.getAllByText("pass").length).toBeGreaterThan(0));

    const confirmBtn = screen.getByRole("button", { name: /save new version/i });
    expect(confirmBtn).not.toBeDisabled();
    // No "promote anyway" checkbox rendered when all scenarios pass.
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
