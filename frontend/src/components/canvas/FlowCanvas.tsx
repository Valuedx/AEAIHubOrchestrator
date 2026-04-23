import { useCallback, useEffect, useRef, type DragEvent } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type ReactFlowInstance,
  type DefaultEdgeOptions,
  BackgroundVariant,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Sparkles } from "lucide-react";

import { useFlowStore } from "@/store/flowStore";
import { useWorkflowStore } from "@/store/workflowStore";
import { AgenticNode } from "@/components/nodes/AgenticNode";
import { StickyNote } from "@/components/nodes/StickyNote";
import { LoopbackEdge } from "@/components/canvas/LoopbackEdge";
import { isTextEditingTarget } from "@/lib/keyboardUtils";
import type { NodeCategory } from "@/types/nodes";

const nodeTypes = { agenticNode: AgenticNode, stickyNote: StickyNote };
// CYCLIC-01.d — loopback edges render with the dashed-amber custom
// renderer. Forward edges keep the default bezier.
const edgeTypes = { loopback: LoopbackEdge };

const defaultEdgeOptions: DefaultEdgeOptions = {
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  style: { strokeWidth: 1.5 },
};

export function FlowCanvas() {
  const reactFlowRef = useRef<ReactFlowInstance | null>(null);

  const baseNodes = useFlowStore((s) => s.nodes);
  const baseEdges = useFlowStore((s) => s.edges);
  const onNodesChange = useFlowStore((s) => s.onNodesChange);
  const onEdgesChange = useFlowStore((s) => s.onEdgesChange);
  const onConnect = useFlowStore((s) => s.onConnect);
  const addNode = useFlowStore((s) => s.addNode);
  const addStickyNote = useFlowStore((s) => s.addStickyNote);
  const selectNode = useFlowStore((s) => s.selectNode);
  const selectEdge = useFlowStore((s) => s.selectEdge);
  const undo = useFlowStore((s) => s.undo);
  const redo = useFlowStore((s) => s.redo);
  const markDirty = useWorkflowStore((s) => s.markDirty);

  // CYCLIC-01.d — clear both selections on pane clicks so the right
  // pane resets to its empty state; clicking an edge opens the
  // EdgeInspector, clicking a node opens the PropertyInspector.
  const onPaneClick = useCallback(() => {
    selectNode(null);
    selectEdge(null);
  }, [selectNode, selectEdge]);

  // COPILOT-02.ii.b — when the copilot panel hands a preview graph
  // to flowStore, render THAT read-only instead of the base
  // nodes/edges. Preview state carries the diff annotations + added
  // counts so we can show a banner above the canvas.
  const preview = useFlowStore((s) => s.copilotPreview);
  const inPreviewMode = preview !== null;
  const nodes = inPreviewMode ? preview!.nodes : baseNodes;
  const edges = inPreviewMode ? preview!.edges : baseEdges;

  // DV-03 — drop a sticky at the current viewport centre. Used by the
  // Shift+S shortcut and the Toolbar "Add sticky" button (via custom
  // event).
  const addStickyAtViewportCenter = useCallback(() => {
    const instance = reactFlowRef.current;
    if (!instance) return;
    const rect = document
      .querySelector(".react-flow")
      ?.getBoundingClientRect();
    const screen = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    const position = instance.screenToFlowPosition(screen);
    // Centre the 220×140 sticky on that point.
    addStickyNote({ x: position.x - 110, y: position.y - 70 });
    markDirty();
  }, [addStickyNote, markDirty]);

  // Keyboard shortcuts: Ctrl+Z → undo, Ctrl+Y / Ctrl+Shift+Z → redo,
  // Shift+S → add sticky at viewport centre, 1 → fit view.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
        markDirty();
        return;
      }
      if (mod && (e.key === "y" || (e.key === "z" && e.shiftKey))) {
        e.preventDefault();
        redo();
        markDirty();
        return;
      }
      // Single-key shortcuts — guard against input/textarea focus so
      // typing "S" into a field doesn't spawn a sticky.
      if (isTextEditingTarget(e.target)) return;
      if (mod || e.altKey) return;
      if (e.shiftKey && (e.key === "S" || e.key === "s")) {
        e.preventDefault();
        addStickyAtViewportCenter();
        return;
      }
      if (!e.shiftKey && e.key === "1") {
        e.preventDefault();
        reactFlowRef.current?.fitView({ padding: 0.15, duration: 300 });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undo, redo, markDirty, addStickyAtViewportCenter]);

  // DV-03 — toolbar button dispatches this event so the canvas (which
  // owns the React Flow instance) performs the insert.
  useEffect(() => {
    const handler = () => addStickyAtViewportCenter();
    window.addEventListener("aeai:add-sticky", handler);
    return () => window.removeEventListener("aeai:add-sticky", handler);
  }, [addStickyAtViewportCenter]);

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/reactflow");
      if (!raw || !reactFlowRef.current) return;

      const { nodeCategory, label, defaultConfig } = JSON.parse(raw) as {
        nodeCategory: NodeCategory;
        label: string;
        defaultConfig: Record<string, unknown>;
      };

      const position = reactFlowRef.current.screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });

      addNode(nodeCategory, label, position, defaultConfig);
      markDirty();
    },
    [addNode, markDirty],
  );

  const handleNodesChange: typeof onNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes);
      const hasMeaningfulChange = changes.some(
        (c) => c.type !== "select" && c.type !== "dimensions",
      );
      if (hasMeaningfulChange) markDirty();
    },
    [onNodesChange, markDirty],
  );

  const handleEdgesChange: typeof onEdgesChange = useCallback(
    (changes) => {
      onEdgesChange(changes);
      if (changes.length > 0) markDirty();
    },
    [onEdgesChange, markDirty],
  );

  const handleConnect: typeof onConnect = useCallback(
    (connection) => {
      onConnect(connection);
      markDirty();
    },
    [onConnect, markDirty],
  );

  return (
    <div className="flex-1 h-full relative">
      {inPreviewMode && <CopilotPreviewBanner preview={preview!} />}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={inPreviewMode ? undefined : handleNodesChange}
        onEdgesChange={inPreviewMode ? undefined : handleEdgesChange}
        onConnect={inPreviewMode ? undefined : handleConnect}
        onInit={(instance) => {
          reactFlowRef.current = instance;
        }}
        onDrop={inPreviewMode ? undefined : onDrop}
        onDragOver={inPreviewMode ? undefined : onDragOver}
        onNodeClick={(_, node) => selectNode(node.id)}
        onEdgeClick={(_, edge) => selectEdge(edge.id)}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        fitView
        // Preview mode is read-only: no drags, no connect-drags, no
        // delete-on-key, no drop-from-palette. Selection stays on so
        // the inspector can still show a clicked node's config if the
        // user opens it (inspector is hidden while copilot open, but
        // the affordance reads cleanly for future splits).
        nodesDraggable={!inPreviewMode}
        nodesConnectable={!inPreviewMode}
        elementsSelectable={true}
        deleteKeyCode={inPreviewMode ? null : ["Backspace", "Delete"]}
        className="bg-background"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
        <Controls className="!bg-card !border !border-border !rounded-lg !shadow-sm" />
        <MiniMap
          className="!bg-card !border !border-border !rounded-lg"
          maskColor="rgba(0, 0, 0, 0.1)"
          pannable
          zoomable
        />
      </ReactFlow>
    </div>
  );
}


// ---------------------------------------------------------------------------
// COPILOT-02.ii.b — preview banner
// ---------------------------------------------------------------------------


function CopilotPreviewBanner({
  preview,
}: {
  preview: import("@/store/flowStore").CopilotPreviewGraph;
}) {
  const added = preview.addedNodeIds.length;
  const modified = preview.modifiedNodeIds.length;
  const parts: string[] = [];
  if (added) parts.push(`${added} added`);
  if (modified) parts.push(`${modified} modified`);
  if (!parts.length) parts.push("no structural changes");
  return (
    <div
      className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-10 rounded-full border border-primary/40 bg-primary/10 backdrop-blur px-3 py-1.5 text-[11px] text-primary flex items-center gap-1.5 shadow-sm"
      aria-live="polite"
      role="status"
    >
      <Sparkles className="h-3 w-3" />
      <span className="font-medium">Copilot draft preview</span>
      <span className="text-muted-foreground">· {parts.join(" · ")}</span>
      <span className="text-muted-foreground">· read-only — Apply in the panel to promote</span>
    </div>
  );
}
