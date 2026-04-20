/**
 * Helpers for rendering the AE-07 "waiting-on-external" badge.
 *
 * All pure: no React, no store access. Keeps the ExecutionPanel row
 * concise and the formatting unit-testable without any DOM.
 */

import type { AsyncJobOut } from "@/lib/api";

/** Human-readable "Xh Ym Zs" with tight zero-suppression. */
export function formatElapsed(ms: number): string {
  if (ms < 0) ms = 0;
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function elapsedMsSince(iso: string | null, now: number = Date.now()): number {
  if (!iso) return 0;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 0;
  return Math.max(0, now - t);
}

/**
 * Compute the badge content for an AE-suspended node.
 *
 * Returns ``null`` when the job has left the waiting state (terminal or
 * never-waiting). Callers branch off null to suppress the badge.
 */
export interface WaitingBadge {
  primary: string;          // "Waiting on AutomationEdge · 2m 14s"
  subLabel?: string;        // "Diverted in AE · awaiting operator · 45s"
  isDiverted: boolean;
}

export function waitingBadgeFor(
  job: AsyncJobOut | undefined,
  now: number = Date.now(),
): WaitingBadge | null {
  if (!job) return null;
  if (!["submitted", "running"].includes(job.status)) return null;

  const elapsedMs = elapsedMsSince(job.submitted_at, now);
  const systemLabel = systemDisplayName(job.system);
  const primary = `Waiting on ${systemLabel} · ${formatElapsed(elapsedMs)}`;

  const isDiverted = job.last_external_status === "Diverted";
  if (!isDiverted) {
    return { primary, isDiverted: false };
  }

  const currentDivertMs = elapsedMsSince(job.diverted_since, now);
  const totalDivertMs = job.total_diverted_ms + currentDivertMs;
  const subLabel =
    `Diverted in ${systemLabel} · awaiting operator · ` +
    `${formatElapsed(currentDivertMs)}` +
    (totalDivertMs > currentDivertMs
      ? ` (total ${formatElapsed(totalDivertMs)})`
      : "");
  return { primary, subLabel, isDiverted: true };
}

function systemDisplayName(system: string): string {
  if (system === "automationedge") return "AutomationEdge";
  // Default: title-case the raw system key — good enough for any future
  // additions until they earn a bespoke label here.
  return system.charAt(0).toUpperCase() + system.slice(1);
}
