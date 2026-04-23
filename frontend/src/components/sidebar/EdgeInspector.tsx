import { useCallback } from "react";
import { X, Repeat, ArrowRight, Trash2 } from "lucide-react";
import { useFlowStore } from "@/store/flowStore";
import { useWorkflowStore } from "@/store/workflowStore";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  LOOPBACK_DEFAULT_MAX_ITERATIONS,
  LOOPBACK_MAX_ITERATIONS_HARD_CAP,
  clampLoopbackMaxIterations,
  isLoopbackEdge,
} from "@/types/edges";

/**
 * CYCLIC-01.d — the right-hand pane shown when the author clicks
 * an edge on the canvas. Currently scopes tightly to loopback edges
 * (forward edges have nothing tunable yet); forward-edge
 * configuration surfaces land in a later slice.
 *
 * Sharing the 288px column with ``PropertyInspector`` keeps a
 * single focused pane on the right — ``App.tsx`` picks whichever
 * inspector matches the current selection.
 */
export function EdgeInspector() {
  const selectedEdgeId = useFlowStore((s) => s.selectedEdgeId);
  const edges = useFlowStore((s) => s.edges);
  const nodes = useFlowStore((s) => s.nodes);
  const selectEdge = useFlowStore((s) => s.selectEdge);
  const updateEdge = useFlowStore((s) => s.updateEdge);
  const onEdgesChange = useFlowStore((s) => s.onEdgesChange);
  const markDirty = useWorkflowStore((s) => s.markDirty);

  const edge = edges.find((e) => e.id === selectedEdgeId);

  const onMaxIterChange = useCallback(
    (raw: string) => {
      if (!edge) return;
      const parsed = Number.parseInt(raw, 10);
      const maxIter = Number.isFinite(parsed)
        ? clampLoopbackMaxIterations(parsed)
        : LOOPBACK_DEFAULT_MAX_ITERATIONS;
      updateEdge(edge.id, {
        data: { ...(edge.data ?? {}), maxIterations: maxIter },
      });
      markDirty();
    },
    [edge, updateEdge, markDirty],
  );

  const onConvertToLoopback = useCallback(() => {
    if (!edge) return;
    updateEdge(edge.id, {
      type: "loopback",
      data: {
        ...(edge.data ?? {}),
        maxIterations:
          (edge.data as { maxIterations?: number } | undefined)
            ?.maxIterations ?? LOOPBACK_DEFAULT_MAX_ITERATIONS,
      },
      // Loopbacks don't render the condition label/style, clear
      // those so a "Convert to loopback" on a condition branch
      // doesn't leave stale green/red styling.
      label: undefined,
      style: undefined,
      animated: false,
    });
    markDirty();
  }, [edge, updateEdge, markDirty]);

  const onConvertToForward = useCallback(() => {
    if (!edge) return;
    updateEdge(edge.id, { type: undefined });
    markDirty();
  }, [edge, updateEdge, markDirty]);

  const onDelete = useCallback(() => {
    if (!edge) return;
    onEdgesChange([{ id: edge.id, type: "remove" }]);
    selectEdge(null);
    markDirty();
  }, [edge, onEdgesChange, selectEdge, markDirty]);

  if (!edge) {
    // Rare: selection briefly points at a deleted edge. Fall back
    // to the empty state that matches the PropertyInspector shape.
    return (
      <div className="flex flex-col items-center justify-center w-72 border-l bg-sidebar text-muted-foreground p-6">
        <p className="text-sm text-center">Edge no longer exists.</p>
      </div>
    );
  }

  const loopback = isLoopbackEdge(edge);
  const sourceNode = nodes.find((n) => n.id === edge.source);
  const targetNode = nodes.find((n) => n.id === edge.target);
  const sourceLabel =
    (sourceNode?.data as { displayName?: string; label?: string } | undefined)
      ?.displayName ||
    (sourceNode?.data as { label?: string } | undefined)?.label ||
    edge.source;
  const targetLabel =
    (targetNode?.data as { displayName?: string; label?: string } | undefined)
      ?.displayName ||
    (targetNode?.data as { label?: string } | undefined)?.label ||
    edge.target;

  const maxIter = clampLoopbackMaxIterations(
    (edge.data as { maxIterations?: unknown } | undefined)?.maxIterations ??
      LOOPBACK_DEFAULT_MAX_ITERATIONS,
  );

  return (
    <div className="flex flex-col w-72 border-l bg-sidebar min-h-0">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold flex items-center gap-1.5">
          {loopback ? (
            <Repeat className="h-4 w-4 text-amber-600" />
          ) : (
            <ArrowRight className="h-4 w-4 text-muted-foreground" />
          )}
          {loopback ? "Loopback edge" : "Forward edge"}
        </h2>
        <button
          onClick={() => selectEdge(null)}
          className="p-1 rounded-md hover:bg-accent transition-colors"
          aria-label="Close edge inspector"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 min-h-0 px-4 py-3">
        <div className="space-y-4">
          {/* Source → target summary */}
          <div className="rounded-md bg-muted px-2.5 py-2 space-y-1">
            <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
              Connection
            </div>
            <div className="flex items-center gap-1 text-xs font-mono truncate">
              <span className="truncate">{sourceLabel}</span>
              <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
              <span className="truncate">{targetLabel}</span>
            </div>
          </div>

          {loopback ? (
            <>
              <div className="space-y-2">
                <Label htmlFor="loopbackMaxIterations">Max iterations</Label>
                <Input
                  id="loopbackMaxIterations"
                  type="number"
                  min={1}
                  max={LOOPBACK_MAX_ITERATIONS_HARD_CAP}
                  value={maxIter}
                  onChange={(e) => onMaxIterChange(e.target.value)}
                />
                <p className="text-[10px] text-muted-foreground leading-relaxed">
                  Upper bound on how many times this back-reference can
                  re-fire per instance. Clamped to 1–
                  {LOOPBACK_MAX_ITERATIONS_HARD_CAP}. When exceeded, the
                  runtime stops looping and continues on forward
                  edges.
                </p>
              </div>

              <Separator />

              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={onConvertToForward}
              >
                <ArrowRight className="h-3.5 w-3.5 mr-1.5" />
                Convert to forward edge
              </Button>
            </>
          ) : (
            <>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                Forward edges flow from source to target once per
                instance. Convert to a loopback if the target is an
                upstream node and you want this edge to re-enqueue the
                target (LangGraph-style cycles).
              </p>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={onConvertToLoopback}
              >
                <Repeat className="h-3.5 w-3.5 mr-1.5" />
                Convert to loopback
              </Button>
            </>
          )}

          <Separator />

          <Button
            variant="ghost"
            size="sm"
            className="w-full text-destructive hover:text-destructive hover:bg-destructive/10"
            onClick={onDelete}
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            Delete edge
          </Button>
        </div>
      </ScrollArea>
    </div>
  );
}
