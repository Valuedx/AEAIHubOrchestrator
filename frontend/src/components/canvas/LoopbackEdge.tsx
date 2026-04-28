import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import { Repeat } from "lucide-react";
import {
  LOOPBACK_DEFAULT_MAX_ITERATIONS,
  clampLoopbackMaxIterations,
} from "@/types/edges";
import { useFlowStore } from "@/store/flowStore";

/**
 * CYCLIC-01.d — custom edge renderer for loopback edges.
 *
 * Differentiates back-references from forward flow at a glance:
 *  - Dashed amber curved stroke (forward edges are solid grey).
 *  - Corner badge showing ``↻ ×N`` where N is ``maxIterations``.
 *  - Selected state thickens the stroke + tints the badge so the
 *    author knows which edge the EdgeInspector is editing.
 *
 * The badge click-target doubles as the selection affordance — a
 * 1-px-thick dashed line is surprisingly hard to click, so we give
 * the operator a chunky chip to aim at.
 */
function LoopbackEdgeImpl({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  selected,
  data,
}: EdgeProps) {
  // A back-reference naturally wants a larger curve than a forward
  // edge — otherwise the path cuts straight through the nodes it's
  // trying to hop over. Nudging curvature higher gives the loop
  // some breathing room.
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.45,
  });

  const selectEdge = useFlowStore((s) => s.selectEdge);

  const maxIter = clampLoopbackMaxIterations(
    (data as { maxIterations?: unknown } | undefined)?.maxIterations ??
      LOOPBACK_DEFAULT_MAX_ITERATIONS,
  );

  // Amber 500 (Tailwind) — consistent with the "loopback" motif used
  // elsewhere (banners, lints). Darker on selection for contrast.
  const stroke = selected ? "#b45309" : "#d97706";

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke,
          strokeWidth: selected ? 2.5 : 2,
          strokeDasharray: "6 4",
        }}
      />
      <EdgeLabelRenderer>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            selectEdge(id);
          }}
          className={`nodrag nopan absolute pointer-events-auto flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium shadow-sm transition-colors ${
            selected
              ? "border-amber-700 bg-amber-100 text-amber-900"
              : "border-amber-500/60 bg-amber-50 text-amber-800 hover:bg-amber-100"
          }`}
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
          aria-label={`Loopback edge, max ${maxIter} iterations — click to configure`}
        >
          <Repeat className="h-2.5 w-2.5" />
          <span>×{maxIter}</span>
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

export const LoopbackEdge = memo(LoopbackEdgeImpl);
