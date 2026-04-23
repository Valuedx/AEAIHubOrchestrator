/**
 * HITL-01.b — smoke tests for the pending-approvals toolbar
 * button.
 *
 * UX invariants we pin:
 *
 *  1. The button shows a refresh icon + bell always.
 *  2. With zero pending rows, the amber pulse + count badge are
 *     NOT rendered — the bell sits quiet.
 *  3. With pending rows, a count badge renders with the exact
 *     count (up to 9, then "9+").
 *  4. Dropdown renders the empty state ("No approvals waiting —
 *     nice.") when count is zero.
 *  5. Dropdown groups rows by workflow name.
 *  6. `formatAge` renders human-friendly relatives.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  PendingApprovalsButton,
  formatAge,
} from "./PendingApprovalsButton";
import { api, type PendingApproval } from "@/lib/api";


function stubRow(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    instance_id: `i-${Math.random().toString(36).slice(2, 8)}`,
    workflow_id: "wf-1",
    workflow_name: "Slack summariser",
    node_id: "node_4",
    approval_message: "Confirm the recipients.",
    suspended_at: new Date(Date.now() - 300_000).toISOString(),
    age_seconds: 300,
    ...overrides,
  };
}


describe("PendingApprovalsButton", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the bell quietly with zero pending", async () => {
    vi.spyOn(api, "listPendingApprovals").mockResolvedValue([]);

    render(<PendingApprovalsButton />);
    await waitFor(() =>
      expect(api.listPendingApprovals).toHaveBeenCalled(),
    );

    const button = screen.getByRole("button", {
      name: /no pending approvals/i,
    });
    // No amber badge in the quiet state — just the bell.
    expect(button.querySelector(".bg-amber-500")).toBeNull();
  });

  it("renders count badge when pending > 0", async () => {
    vi.spyOn(api, "listPendingApprovals").mockResolvedValue([
      stubRow({ instance_id: "a" }),
      stubRow({ instance_id: "b" }),
      stubRow({ instance_id: "c" }),
    ]);

    render(<PendingApprovalsButton />);

    await waitFor(() => {
      const button = screen.getByRole("button", {
        name: /3 pending approvals/i,
      });
      // Pulsing amber dot present.
      expect(button.querySelector(".bg-amber-500")).not.toBeNull();
      // Count badge visible.
      expect(button).toHaveTextContent("3");
    });
  });

  it("caps the count badge at 9+", async () => {
    const rows = Array.from({ length: 12 }).map((_, i) =>
      stubRow({ instance_id: `r-${i}` }),
    );
    vi.spyOn(api, "listPendingApprovals").mockResolvedValue(rows);

    render(<PendingApprovalsButton />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /12 pending approvals/i }),
      ).toHaveTextContent("9+"),
    );
  });

  it("shows the empty state when opened with no pending rows", async () => {
    vi.spyOn(api, "listPendingApprovals").mockResolvedValue([]);
    render(<PendingApprovalsButton />);
    await waitFor(() =>
      expect(api.listPendingApprovals).toHaveBeenCalled(),
    );

    fireEvent.click(screen.getByRole("button", { name: /no pending approvals/i }));
    await waitFor(() => {
      expect(screen.getByText(/No approvals waiting/i)).toBeInTheDocument();
    });
  });

  it("groups rows in the dropdown by workflow name", async () => {
    vi.spyOn(api, "listPendingApprovals").mockResolvedValue([
      stubRow({ instance_id: "a", workflow_id: "wf-1", workflow_name: "Slack summariser" }),
      stubRow({ instance_id: "b", workflow_id: "wf-1", workflow_name: "Slack summariser" }),
      stubRow({ instance_id: "c", workflow_id: "wf-2", workflow_name: "Incident triager" }),
    ]);

    render(<PendingApprovalsButton />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /3 pending approvals/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: /3 pending approvals/i }));

    await waitFor(() => {
      // Two workflow headers — one per unique workflow name.
      expect(screen.getAllByText("Slack summariser").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Incident triager").length).toBeGreaterThan(0);
    });
  });
});


describe("formatAge", () => {
  it.each([
    [0, "just now"],
    [45, "just now"],
    [120, "2m ago"],
    [3 * 3600, "3h ago"],
    [30 * 3600, "1d ago"],
    [3 * 86400, "3d ago"],
  ] as Array<[number, string]>)(
    "%i seconds → %s",
    (seconds, expected) => {
      expect(formatAge(seconds)).toBe(expected);
    },
  );
});
