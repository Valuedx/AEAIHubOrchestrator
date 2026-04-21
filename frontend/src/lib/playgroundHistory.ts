/**
 * API-18A — localStorage-backed "last 10 runs" history for the Playground.
 *
 * Scoped per workflow id so switching workflows shows its own history.
 * Capped so we don't let the ring buffer grow unbounded across months
 * of iteration. Non-critical storage — every accessor catches the
 * localStorage errors (private-mode / quota / SSR) so the dialog
 * renders even when history is unavailable.
 */

export interface PlaygroundHistoryEntry {
  /** Epoch milliseconds. */
  at: number;
  /** Matches the execute flag so we can show a badge. */
  mode: "sync" | "async";
  /** Terminal status, ``"pending"`` for async runs, or ``"error"``. */
  status: string;
  /** Wall-clock elapsed for sync runs; null when unknown. */
  elapsed_ms: number | null;
  /** The exact JSON string the user ran, so "Load payload" is deterministic. */
  payload: string;
  /** Instance id returned by the backend, if any. */
  instance_id: string | null;
}

export const HISTORY_LIMIT = 10;

function _key(workflowId: string): string {
  return `aeai:playground:${workflowId}:history`;
}

export function loadHistory(workflowId: string): PlaygroundHistoryEntry[] {
  try {
    const raw = window.localStorage.getItem(_key(workflowId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Defensive filter — drop entries that don't match the shape so a
    // stale write from an older schema can't crash the dialog.
    return parsed
      .filter((e): e is PlaygroundHistoryEntry =>
        !!e && typeof e.at === "number" && typeof e.payload === "string"
          && (e.mode === "sync" || e.mode === "async")
          && typeof e.status === "string",
      )
      .slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

export function addToHistory(
  workflowId: string,
  entry: PlaygroundHistoryEntry,
): PlaygroundHistoryEntry[] {
  const existing = loadHistory(workflowId);
  const next = [entry, ...existing].slice(0, HISTORY_LIMIT);
  try {
    window.localStorage.setItem(_key(workflowId), JSON.stringify(next));
  } catch {
    // localStorage may be unavailable (SSR, private mode, quota). The
    // in-memory return value is still useful to the caller.
  }
  return next;
}

export function clearHistory(workflowId: string): void {
  try {
    window.localStorage.removeItem(_key(workflowId));
  } catch {
    // Intentionally silent — see loadHistory rationale.
  }
}
