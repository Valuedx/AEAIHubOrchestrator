import { describe, it, expect } from "vitest";
import { nextBackoffMs, POLL_BASE_MS, POLL_MAX_MS } from "./retry";

describe("nextBackoffMs", () => {
  it("returns base for attempt 1", () => {
    expect(nextBackoffMs(1)).toBe(POLL_BASE_MS);
  });

  it("doubles each attempt up to the cap", () => {
    expect(nextBackoffMs(1)).toBe(1500);
    expect(nextBackoffMs(2)).toBe(3000);
    expect(nextBackoffMs(3)).toBe(6000);
    expect(nextBackoffMs(4)).toBe(12000);
  });

  it("is capped at POLL_MAX_MS", () => {
    expect(nextBackoffMs(5)).toBe(POLL_MAX_MS);
    expect(nextBackoffMs(20)).toBe(POLL_MAX_MS);
    expect(nextBackoffMs(100)).toBe(POLL_MAX_MS);
  });

  it("clamps non-positive attempts to base", () => {
    expect(nextBackoffMs(0)).toBe(POLL_BASE_MS);
    expect(nextBackoffMs(-3)).toBe(POLL_BASE_MS);
  });

  it("honours a custom base and cap", () => {
    expect(nextBackoffMs(1, 100, 1000)).toBe(100);
    expect(nextBackoffMs(3, 100, 1000)).toBe(400);
    expect(nextBackoffMs(10, 100, 1000)).toBe(1000);
  });
});
