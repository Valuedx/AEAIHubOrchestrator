/**
 * CYCLIC-01.a — edge-level types for loopback edges.
 *
 * React Flow already types edges via its `Edge` generic; this file
 * adds the small shim our graph_json needs so a loopback edge
 * survives the round-trip through the backend's edge schema
 * (``type == "loopback"`` + top-level ``maxIterations``).
 *
 * The canvas-level renderer for loopback edges lands in
 * CYCLIC-01.d; the runtime semantics (actually cycling back to the
 * target) land in CYCLIC-01.b.
 */

/** Possible `type` values on an edge in graph_json. React Flow
 *  leaves this as an open string; we narrow to known variants. */
export type GraphEdgeKind = "forward" | "loopback";

/** Default iteration cap for a loopback edge when the author
 *  omits `maxIterations`. Kept aligned with the existing Loop
 *  node's default so operators don't memorise two numbers. */
export const LOOPBACK_DEFAULT_MAX_ITERATIONS = 10;

/** Hard ceiling the backend clamps to regardless of the edge's
 *  configured value — prevents runaway loops from a typo like
 *  `maxIterations: 999999`. Validator (CYCLIC-01.c) surfaces a
 *  warning when the author-supplied value exceeds this, and an
 *  error when the value is below 1. */
export const LOOPBACK_MAX_ITERATIONS_HARD_CAP = 100;

/**
 * Type guard — is this edge a loopback edge? Used by the custom
 * edge renderer in CYCLIC-01.d and the cycle-aware validator in
 * CYCLIC-01.c.
 */
export function isLoopbackEdge(
  edge: { type?: string | null },
): boolean {
  return edge.type === "loopback";
}

/**
 * Normalise an edge-level `maxIterations` attribute off a React
 * Flow edge, clamping to [1, 100] and filling the default when
 * the attribute is missing. Returns the integer ready to persist
 * or display — no null branches downstream.
 */
export function clampLoopbackMaxIterations(
  raw: unknown,
): number {
  const parsed = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(parsed)) return LOOPBACK_DEFAULT_MAX_ITERATIONS;
  return Math.max(
    1,
    Math.min(Math.floor(parsed), LOOPBACK_MAX_ITERATIONS_HARD_CAP),
  );
}

// ---------------------------------------------------------------------------
// graph_json interop
// ---------------------------------------------------------------------------

/**
 * CYCLIC-01.d — serialise edges for persistence. The backend's
 * loopback edge schema expects ``maxIterations`` at the top level
 * (see ``backend/app/engine/cyclic_analysis.py::get_loopback_max_iterations``),
 * but React Flow stores custom edge attributes under ``data``. This
 * helper lifts ``data.maxIterations`` up to the edge root so the
 * saved graph_json matches the backend schema.
 *
 * Forward edges and loopback edges without a configured cap are
 * passed through unchanged. This keeps the save-time transformation
 * minimal — no structural rewrite, just one field lifted when it
 * exists.
 */
export function serialiseEdgesForSave<
  T extends { type?: string; data?: unknown; maxIterations?: number | null }
>(edges: T[]): T[] {
  return edges.map((e) => {
    if (e.type !== "loopback") return e;
    const maxIter = (e.data as { maxIterations?: unknown } | undefined)
      ?.maxIterations;
    if (maxIter === undefined || maxIter === null) return e;
    return { ...e, maxIterations: clampLoopbackMaxIterations(maxIter) };
  });
}

/**
 * CYCLIC-01.d — inverse of ``serialiseEdgesForSave``. When hydrating
 * graph_json from the backend (workflow load, version restore), lift
 * top-level ``maxIterations`` into ``data.maxIterations`` so the
 * LoopbackEdge renderer can read it via React Flow's standard
 * ``EdgeProps.data`` surface.
 *
 * Leaves forward edges untouched. Preserves any other ``data`` keys
 * the backend or copilot may have attached to the edge.
 */
export function hydrateEdgesFromLoad<
  T extends { type?: string; data?: unknown; maxIterations?: number | null }
>(edges: T[]): T[] {
  return edges.map((e) => {
    if (e.type !== "loopback") return e;
    if (e.maxIterations === undefined || e.maxIterations === null) return e;
    const existingData = (e.data as Record<string, unknown> | undefined) ?? {};
    return {
      ...e,
      data: {
        ...existingData,
        maxIterations: clampLoopbackMaxIterations(e.maxIterations),
      },
    };
  });
}
