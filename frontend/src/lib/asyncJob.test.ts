import { describe, expect, it } from "vitest";

import {
  elapsedMsSince,
  formatElapsed,
  waitingBadgeFor,
} from "./asyncJob";
import type { AsyncJobOut } from "@/lib/api";

const NOW = Date.parse("2026-04-21T12:00:00Z");


function job(partial: Partial<AsyncJobOut>): AsyncJobOut {
  return {
    id: "j",
    instance_id: "i",
    node_id: "node_1",
    system: "automationedge",
    external_job_id: "42",
    status: "running",
    submitted_at: new Date(NOW - 134_000).toISOString(),  // 2m 14s ago
    last_polled_at: null,
    completed_at: null,
    last_external_status: "Executing",
    total_diverted_ms: 0,
    diverted_since: null,
    last_error: null,
    ...partial,
  };
}


describe("formatElapsed", () => {
  it("zero-suppresses below a minute", () => {
    expect(formatElapsed(500)).toBe("0s");
    expect(formatElapsed(1_000)).toBe("1s");
    expect(formatElapsed(59_000)).toBe("59s");
  });

  it("shows minutes and seconds between 1m and 1h", () => {
    expect(formatElapsed(60_000)).toBe("1m 0s");
    expect(formatElapsed(134_000)).toBe("2m 14s");
    expect(formatElapsed(3_599_000)).toBe("59m 59s");
  });

  it("collapses to hours and minutes past the hour mark", () => {
    expect(formatElapsed(3_600_000)).toBe("1h 0m");
    expect(formatElapsed(7_320_000)).toBe("2h 2m");
    expect(formatElapsed(86_400_000)).toBe("24h 0m");
  });

  it("clamps negatives to zero", () => {
    expect(formatElapsed(-50_000)).toBe("0s");
  });
});


describe("elapsedMsSince", () => {
  it("returns ms between iso and now", () => {
    expect(elapsedMsSince(new Date(NOW - 5000).toISOString(), NOW)).toBe(5000);
  });

  it("returns 0 for null", () => {
    expect(elapsedMsSince(null, NOW)).toBe(0);
  });

  it("clamps negatives (future timestamps) to 0", () => {
    expect(elapsedMsSince(new Date(NOW + 10_000).toISOString(), NOW)).toBe(0);
  });

  it("returns 0 for unparseable strings", () => {
    expect(elapsedMsSince("not-an-iso", NOW)).toBe(0);
  });
});


describe("waitingBadgeFor", () => {
  it("builds an 'AutomationEdge · elapsed' primary label", () => {
    const b = waitingBadgeFor(job({}), NOW);
    expect(b).not.toBeNull();
    expect(b!.primary).toBe("Waiting on AutomationEdge · 2m 14s");
    expect(b!.isDiverted).toBe(false);
    expect(b!.subLabel).toBeUndefined();
  });

  it("adds a Diverted sub-label with current and total times", () => {
    const b = waitingBadgeFor(
      job({
        last_external_status: "Diverted",
        diverted_since: new Date(NOW - 45_000).toISOString(),
        total_diverted_ms: 120_000,   // prior banked span
      }),
      NOW,
    );
    expect(b!.isDiverted).toBe(true);
    expect(b!.subLabel).toContain("Diverted in AutomationEdge");
    expect(b!.subLabel).toContain("awaiting operator");
    expect(b!.subLabel).toContain("45s");
    // total = banked 120s + current 45s = 165s = 2m 45s
    expect(b!.subLabel).toContain("total 2m 45s");
  });

  it("omits the total clause when there's no prior divert span", () => {
    const b = waitingBadgeFor(
      job({
        last_external_status: "Diverted",
        diverted_since: new Date(NOW - 10_000).toISOString(),
        total_diverted_ms: 0,
      }),
      NOW,
    );
    expect(b!.subLabel).not.toContain("(total");
  });

  it("returns null for terminal jobs (no badge rendered)", () => {
    for (const status of ["completed", "failed", "cancelled", "timed_out"]) {
      expect(waitingBadgeFor(job({ status }), NOW)).toBeNull();
    }
  });

  it("returns null when no job is given", () => {
    expect(waitingBadgeFor(undefined, NOW)).toBeNull();
  });

  it("uses title-case fallback for unknown systems", () => {
    const b = waitingBadgeFor(job({ system: "jenkins" }), NOW);
    expect(b!.primary).toContain("Jenkins");
  });
});
