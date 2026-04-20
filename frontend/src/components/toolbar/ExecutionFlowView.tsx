/**
 * Read-only mini canvas rendered inside ExecutionPanel when the
 * Logs/Flow toggle is flipped to Flow.
 *
 * Reuses the same flowStore nodes/edges that FlowCanvas renders on
 * the main editing surface — so the per-node live status overlays
 * driven by FV-01 appear here without any extra plumbing. The key
 * differences vs. FlowCanvas:
 *
 *   * nodesDraggable / nodesConnectable / edgesUpdatable all false
 *   * panOnDrag + zoomOnScroll stay true so operators can still
 *     explore large DAGs
 *   * Fit-to-view on mount so the whole graph is visible by default
 *   * No MiniMap / Controls (cramped bottom panel; ExecutionPanel
 *     is already the control surface)
 *
 * Lazy-mounted: ExecutionPanel only renders this when the Flow tab
 * is active, so the React Flow cost is only paid when the operator
 * opts in.
 */

import { useEffect } from "react";
import {
  Background,
  BackgroundVariant,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type DefaultEdgeOptions,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useFlowStore } from "@/store/flowStore";
import { AgenticNode } from "@/components/nodes/AgenticNode";

// Module-top-level per xyflow perf guidance — recreating nodeTypes in
// render would remount every node on each parent re-render.
const nodeTypes = { agenticNode: AgenticNode };

const defaultEdgeOptions: DefaultEdgeOptions = {
  markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
  style: { strokeWidth: 1.4 },
};


function ExecutionFlowInner() {
  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);
  const { fitView } = useReactFlow();

  // Fit-to-view once when this component first becomes visible. Not on
  // every nodes-array change — that would jitter the viewport each time
  // a live status flips.
  useEffect(() => {
    fitView({ padding: 0.15, duration: 300 });
    // Intentionally empty deps — we want this to run ONCE on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (nodes.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-muted-foreground">
        Load or build a workflow on the canvas to see its flow here.
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      defaultEdgeOptions={defaultEdgeOptions}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      elementsSelectable={true}
      panOnDrag
      zoomOnScroll
      zoomOnPinch
      minZoom={0.2}
      maxZoom={1.5}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      proOptions={{ hideAttribution: true }}
      className="bg-muted/20"
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
    </ReactFlow>
  );
}


export function ExecutionFlowView() {
  return (
    <ReactFlowProvider>
      <ExecutionFlowInner />
    </ReactFlowProvider>
  );
}
