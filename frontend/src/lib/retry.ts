export const POLL_BASE_MS = 1500;
export const POLL_MAX_MS = 12000;
export const POLL_MAX_ATTEMPTS = 10;

/** Exponential backoff with cap. Attempt starts at 1.
 *  1 -> 1500, 2 -> 3000, 3 -> 6000, 4 -> 12000, 5..n -> 12000.
 */
export function nextBackoffMs(
  attempt: number,
  base: number = POLL_BASE_MS,
  cap: number = POLL_MAX_MS,
): number {
  if (attempt < 1) return base;
  const ms = base * 2 ** (attempt - 1);
  return Math.min(ms, cap);
}
