import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Bug,
  X,
  Loader2,
  AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useWorkflowStore } from "@/store/workflowStore";
import { useFlowStore } from "@/store/flowStore";

export function DebugReplayBar() {
  const [ctxOpen, setCtxOpen] = useState(true);
  const isDebugMode = useWorkflowStore((s) => s.isDebugMode);
  const checkpoints = useWorkflowStore((s) => s.debugCheckpoints);
  const idx = useWorkflowStore((s) => s.activeCheckpointIdx);
  const detail = useWorkflowStore((s) => s.activeCheckpointDetail);
  const loading = useWorkflowStore((s) => s.debugLoading);
  const activeInstance = useWorkflowStore((s) => s.activeInstance);
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const canvasDefinitionVersion = useWorkflowStore((s) => s.canvasDefinitionVersion);
  const exitDebugMode = useWorkflowStore((s) => s.exitDebugMode);
  const selectCheckpointIdx = useWorkflowStore((s) => s.selectCheckpointIdx);
  const stepDebugPrev = useWorkflowStore((s) => s.stepDebugPrev);
  const stepDebugNext = useWorkflowStore((s) => s.stepDebugNext);

  const canvasNodeIds = useFlowStore((s) => s.nodes.map((n) => n.id));

  const mismatchedNodes = useMemo(() => {
    if (checkpoints.length === 0) return [];
    const canvasSet = new Set(canvasNodeIds);
    return checkpoints
      .map((cp) => cp.node_id)
      .filter((nid) => !canvasSet.has(nid));
  }, [checkpoints, canvasNodeIds]);

  const versionDrift =
    activeInstance?.definition_version_at_start != null &&
    canvasDefinitionVersion != null &&
    activeInstance.definition_version_at_start !== canvasDefinitionVersion;

  if (!isDebugMode) return null;

  return (
    <div className="border-t bg-muted/30 px-4 py-2 space-y-2 shrink-0">
      <div className="flex items-center gap-2 flex-wrap">
        <Bug className="h-3.5 w-3.5 text-indigo-600 shrink-0" />
        <span className="text-xs font-medium">Replay</span>
        {idx != null && checkpoints.length > 0 && (
          <Badge variant="secondary" className="text-[10px]">
            Step {idx + 1} / {checkpoints.length}
          </Badge>
        )}
        {detail && (
          <Badge variant="outline" className="text-[10px] font-mono truncate max-w-[140px]">
            @{detail.node_id}
          </Badge>
        )}
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2"
          disabled={loading || idx == null || idx <= 0}
          onClick={() => void stepDebugPrev()}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2"
          disabled={loading || idx == null || idx >= checkpoints.length - 1}
          onClick={() => void stepDebugNext()}
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
        <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => exitDebugMode()}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {versionDrift && (
        <div className="flex items-start gap-2 rounded-md border border-amber-400/60 bg-amber-50 dark:bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-900 dark:text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>
            This run started on definition version {activeInstance!.definition_version_at_start}, but the
            canvas is currently showing definition version {canvasDefinitionVersion}. The saved workflow is
            version {currentWorkflow!.version}. Replay overlays may not match the executed graph.
          </span>
        </div>
      )}

      {mismatchedNodes.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-yellow-400/60 bg-yellow-50 dark:bg-yellow-950/30 px-3 py-1.5 text-[11px] text-yellow-800 dark:text-yellow-300">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>
            The canvas graph differs from the executed run — {mismatchedNodes.length} checkpoint
            node{mismatchedNodes.length > 1 ? "s" : ""} ({mismatchedNodes.join(", ")}) not found on the
            canvas. Overlays may be inaccurate.
          </span>
        </div>
      )}

      {checkpoints.length === 0 ? (
        <p className="text-xs text-muted-foreground">No checkpoints for this run (nodes may not have completed).</p>
      ) : (
        <div className="flex items-center gap-1 overflow-x-auto pb-1">
          {checkpoints.map((cp, i) => (
            <button
              key={cp.id}
              type="button"
              title={`${cp.node_id} · ${cp.saved_at ?? ""}`}
              onClick={() => void selectCheckpointIdx(i)}
              className={`h-2.5 w-2.5 rounded-full shrink-0 transition-transform ${
                i === idx ? "bg-indigo-600 scale-125 ring-2 ring-indigo-300" : "bg-muted-foreground/40 hover:bg-muted-foreground/70"
              }`}
            />
          ))}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading checkpoint…
        </div>
      )}

      {detail && (
        <>
          <button
            type="button"
            onClick={() => setCtxOpen(!ctxOpen)}
            className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground w-full"
          >
            Context at this checkpoint
            {ctxOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
          {ctxOpen && (
            <>
              <Separator />
              <ScrollArea className="max-h-40 rounded-md border bg-background">
                <pre className="text-[10px] font-mono p-2 whitespace-pre-wrap break-all">
                  {JSON.stringify(detail.context_json, null, 2)}
                </pre>
              </ScrollArea>
            </>
          )}
        </>
      )}
    </div>
  );
}
