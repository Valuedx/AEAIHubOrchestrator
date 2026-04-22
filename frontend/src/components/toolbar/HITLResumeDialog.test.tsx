/**
 * HITL-01.a — smoke tests for the redesigned approval dialog.
 *
 * UX invariants we pin:
 *
 *   1. First-time approver sees a blue "stored locally" hint.
 *   2. Returning approver gets their name pre-filled from
 *      localStorage + no hint.
 *   3. Submitting with an empty approver surfaces an inline error
 *      AND does not call the store's resumeInstance.
 *   4. Rejecting without a reason surfaces an inline error.
 *   5. A successful approve/reject persists the approver to
 *      localStorage AND forwards {approver, decision, reason} to
 *      resumeInstance.
 */

import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { HITLResumeDialog } from "./HITLResumeDialog";
import { useWorkflowStore } from "@/store/workflowStore";


const APPROVER_STORAGE_KEY = "aeai.hitl.approver";


const baseContext = {
  instance_id: "i-1",
  status: "suspended",
  current_node_id: "node_4",
  approval_message: "Confirm the recipient list is correct.",
  context_json: { trigger: { msg: "hi" }, node_3: { score: 0.9 } },
};


describe("HITLResumeDialog", () => {
  beforeEach(() => {
    // Reset localStorage and the store's resumeInstance between tests.
    try {
      window.localStorage.removeItem(APPROVER_STORAGE_KEY);
    } catch {
      /* no-op */
    }
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the stored-locally hint when no approver is cached", () => {
    render(
      <HITLResumeDialog
        open
        onClose={vi.fn()}
        workflowId="wf-1"
        instanceId="i-1"
        context={baseContext}
      />,
    );
    expect(screen.getByText(/Stored locally/i)).toBeInTheDocument();
  });

  it("pre-fills the approver input from localStorage", async () => {
    window.localStorage.setItem(APPROVER_STORAGE_KEY, "alice@acme.example");
    render(
      <HITLResumeDialog
        open
        onClose={vi.fn()}
        workflowId="wf-1"
        instanceId="i-1"
        context={baseContext}
      />,
    );
    const input = screen.getByLabelText(/Approving as/i) as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe("alice@acme.example"));
    // Returning approver suppresses the hint — single-shot UX for
    // new-device orientation only.
    expect(screen.queryByText(/Stored locally/i)).not.toBeInTheDocument();
  });

  it("blocks approve with an empty approver + inline error", async () => {
    const spy = vi
      .spyOn(useWorkflowStore.getState(), "resumeInstance")
      .mockResolvedValue(undefined);

    render(
      <HITLResumeDialog
        open
        onClose={vi.fn()}
        workflowId="wf-1"
        instanceId="i-1"
        context={baseContext}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /approve & resume/i }));
    await waitFor(() =>
      expect(screen.getByText(/Enter your name/i)).toBeInTheDocument(),
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("blocks reject without a reason + inline error", async () => {
    window.localStorage.setItem(APPROVER_STORAGE_KEY, "alice");
    const spy = vi
      .spyOn(useWorkflowStore.getState(), "resumeInstance")
      .mockResolvedValue(undefined);

    render(
      <HITLResumeDialog
        open
        onClose={vi.fn()}
        workflowId="wf-1"
        instanceId="i-1"
        context={baseContext}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/leaves the next approver guessing/i),
      ).toBeInTheDocument(),
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("approves with approver + stores name + forwards options", async () => {
    const spy = vi
      .spyOn(useWorkflowStore.getState(), "resumeInstance")
      .mockResolvedValue(undefined);
    const onClose = vi.fn();

    render(
      <HITLResumeDialog
        open
        onClose={onClose}
        workflowId="wf-1"
        instanceId="i-1"
        context={baseContext}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Approving as/i), {
      target: { value: "alice@acme.example" },
    });
    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: "Double-checked with ops." },
    });
    fireEvent.click(screen.getByRole("button", { name: /approve & resume/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    const [, , approvalPayload, patch, options] = spy.mock.calls[0];
    expect(approvalPayload).toEqual({ approved: true });
    expect(patch).toBeUndefined();
    expect(options).toEqual({
      approver: "alice@acme.example",
      decision: "approved",
      reason: "Double-checked with ops.",
    });
    // Approver persisted for next time.
    expect(window.localStorage.getItem(APPROVER_STORAGE_KEY)).toBe("alice@acme.example");
    expect(onClose).toHaveBeenCalled();
  });

  it("rejects with reason + closes + forwards decision=rejected", async () => {
    window.localStorage.setItem(APPROVER_STORAGE_KEY, "bob");
    const spy = vi
      .spyOn(useWorkflowStore.getState(), "resumeInstance")
      .mockResolvedValue(undefined);
    const onClose = vi.fn();

    render(
      <HITLResumeDialog
        open
        onClose={onClose}
        workflowId="wf-1"
        instanceId="i-1"
        context={baseContext}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Reason/i), {
      target: { value: "Wrong recipient list." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const [, , approvalPayload, , options] = spy.mock.calls[0];
    expect(approvalPayload).toEqual({ rejected: true });
    expect(options?.decision).toBe("rejected");
    expect(options?.reason).toBe("Wrong recipient list.");
    expect(onClose).toHaveBeenCalled();
  });
});
