import {
  CircleCheck,
  CircleX,
  CircleDot,
  Loader2,
  Pause,
  PauseCircle,
  Ban,
  StopCircle,
  Play,
  X,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  Maximize2,
  ClipboardCheck,
  Bug,
  Layers,
} from "lucide-react";
import { useState, useCallback, useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorkflowStore } from "@/store/workflowStore";
import { HITLResumeDialog } from "@/components/toolbar/HITLResumeDialog";
import { DebugReplayBar } from "@/components/toolbar/DebugReplayBar";
import { api, type ExecutionLogOut, type ChildInstanceSummary } from "@/lib/api";

// ---------------------------------------------------------------------------
// CopyButton — clipboard copy with 2s checkmark confirmation
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      title={copied ? "Copied!" : "Copy to clipboard"}
      className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground shrink-0"
    >
      {copied ? (
        <Check className="h-3 w-3 text-green-500" />
      ) : (
        <Copy className="h-3 w-3" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// FullJsonDialog — full-size JSON viewer in a dialog
// ---------------------------------------------------------------------------

function FullJsonDialog({
  open,
  onClose,
  title,
  data,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  data: unknown;
}) {
  const json = JSON.stringify(data, null, 2);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl w-full">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="text-sm">{title}</DialogTitle>
            <CopyButton text={json} />
          </div>
        </DialogHeader>
        <ScrollArea className="max-h-[70vh]">
          <pre className="text-xs font-mono bg-muted rounded-md p-4 whitespace-pre-wrap break-all">
            {json}
          </pre>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// JsonBlock — collapsible JSON preview with copy + expand buttons
// ---------------------------------------------------------------------------

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const json = JSON.stringify(data, null, 2);

  return (
    <div>
      <div className="flex items-center gap-1 mb-0.5">
        <p className="text-[10px] font-medium text-muted-foreground flex-1">{label}</p>
        <CopyButton text={json} />
        <button
          onClick={() => setDialogOpen(true)}
          title="View full output"
          className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground"
        >
          <Maximize2 className="h-3 w-3" />
        </button>
      </div>
      <pre className="text-xs bg-muted rounded p-2 overflow-x-auto max-h-32 font-mono">
        {json}
      </pre>
      <FullJsonDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={label}
        data={data}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status maps
// ---------------------------------------------------------------------------

const STATUS_ICON: Record<string, typeof CircleDot> = {
  pending: CircleDot,
  running: Loader2,
  completed: CircleCheck,
  failed: CircleX,
  suspended: Pause,
  paused: PauseCircle,
  cancelled: Ban,
  queued: CircleDot,
};

const STATUS_COLOR: Record<string, string> = {
  pending: "text-muted-foreground",
  running: "text-blue-500",
  completed: "text-green-500",
  failed: "text-red-500",
  suspended: "text-yellow-500",
  cancelled: "text-orange-500",
  queued: "text-muted-foreground",
};

// ---------------------------------------------------------------------------
// ChildInstanceLogs — inline drill-down for sub-workflow child instances
// ---------------------------------------------------------------------------

function ChildInstanceLogs({
  childInstanceId,
  childWorkflowName,
  workflowDefId,
}: {
  childInstanceId: string;
  childWorkflowName?: string;
  workflowDefId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [logs, setLogs] = useState<ExecutionLogOut[]>([]);
  const [childStatus, setChildStatus] = useState<string>("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    setLoading(true);
    api.getInstanceDetail(workflowDefId, childInstanceId).then((detail) => {
      setLogs(detail.logs);
      setChildStatus(detail.status);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [expanded, childInstanceId, workflowDefId]);

  const StatusIcon = STATUS_ICON[childStatus] ?? CircleDot;
  const statusColor = STATUS_COLOR[childStatus] ?? "text-muted-foreground";

  return (
    <div className="border border-dashed rounded-md bg-muted/20">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-accent/50 transition-colors"
      >
        <Layers className="h-3 w-3 text-emerald-500 shrink-0" />
        <span className="text-xs font-medium truncate flex-1">
          Child: {childWorkflowName || childInstanceId.slice(0, 8)}
        </span>
        {childStatus && (
          <Badge variant="outline" className={`text-[9px] px-1 py-0 shrink-0 ${statusColor}`}>
            {childStatus}
          </Badge>
        )}
        {expanded ? (
          <ChevronUp className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="px-2.5 pb-2 space-y-1">
          <Separator />
          {loading ? (
            <p className="text-[10px] text-muted-foreground py-1">Loading child logs...</p>
          ) : logs.length === 0 ? (
            <p className="text-[10px] text-muted-foreground py-1">No logs for child instance.</p>
          ) : (
            logs.map((log) => (
              <LogEntry key={log.id} log={log} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function LogEntry({
  log,
  streamingText,
}: {
  log: ExecutionLogOut;
  streamingText?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const Icon = STATUS_ICON[log.status] ?? CircleDot;
  const color = STATUS_COLOR[log.status] ?? "text-muted-foreground";

  const duration =
    log.started_at && log.completed_at
      ? `${((new Date(log.completed_at).getTime() - new Date(log.started_at).getTime()) / 1000).toFixed(1)}s`
      : null;

  const isSubWorkflow = log.node_type?.includes("Sub-Workflow");
  const childInstanceId = isSubWorkflow
    ? (log.output_json as Record<string, unknown> | null)?.child_instance_id as string | undefined
    : undefined;
  const childWorkflowName = isSubWorkflow
    ? (log.output_json as Record<string, unknown> | null)?.child_workflow_name as string | undefined
    : undefined;
  const childWorkflowDefId = isSubWorkflow
    ? (log.input_json as Record<string, unknown> | null)?.config as Record<string, unknown> | undefined
    : undefined;
  const workflowDefIdForChild = childWorkflowDefId?.workflowId as string | undefined;

  return (
    <div className="border rounded-md">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-accent/50 transition-colors"
      >
        <Icon
          className={`h-3.5 w-3.5 shrink-0 ${color} ${log.status === "running" ? "animate-spin" : ""}`}
        />
        <span className="text-sm font-medium truncate flex-1">
          {log.node_type}
        </span>
        {duration && (
          <span className="text-[10px] text-muted-foreground shrink-0">{duration}</span>
        )}
        <Badge
          variant="outline"
          className={`text-[10px] px-1 py-0 shrink-0 ${color}`}
        >
          {log.status}
        </Badge>
        {expanded ? (
          <ChevronUp className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1.5">
          <Separator />
          {log.error && (
            <div className="text-xs text-red-500 bg-red-50 dark:bg-red-950/30 rounded p-2 font-mono whitespace-pre-wrap">
              {log.error}
            </div>
          )}
          {/* Child instance drill-down for Sub-Workflow nodes */}
          {childInstanceId && workflowDefIdForChild && (
            <ChildInstanceLogs
              childInstanceId={childInstanceId}
              childWorkflowName={childWorkflowName}
              workflowDefId={workflowDefIdForChild}
            />
          )}
          {/* Live token stream — shown for running nodes while the LLM is generating */}
          {log.status === "running" && streamingText && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground mb-0.5 flex items-center gap-1">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
                Generating…
              </p>
              <pre className="text-xs bg-muted rounded p-2 max-h-32 overflow-y-auto font-mono whitespace-pre-wrap break-words">
                {streamingText}
              </pre>
            </div>
          )}
          {log.output_json && (
            <JsonBlock label="Output" data={log.output_json} />
          )}
          {log.input_json && (
            <JsonBlock label="Input" data={log.input_json} />
          )}
        </div>
      )}
    </div>
  );
}

export function ExecutionPanel() {
  const activeInstance = useWorkflowStore((s) => s.activeInstance);
  const isExecuting = useWorkflowStore((s) => s.isExecuting);
  const clearExecution = useWorkflowStore((s) => s.clearExecution);
  const cancelInstance = useWorkflowStore((s) => s.cancelInstance);
  const pauseInstance = useWorkflowStore((s) => s.pauseInstance);
  const resumePausedInstance = useWorkflowStore((s) => s.resumePausedInstance);
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const instanceContext = useWorkflowStore((s) => s.instanceContext);
  const fetchInstanceContext = useWorkflowStore((s) => s.fetchInstanceContext);
  const streamingTokens = useWorkflowStore((s) => s.streamingTokens);
  const isDebugMode = useWorkflowStore((s) => s.isDebugMode);
  const enterDebugMode = useWorkflowStore((s) => s.enterDebugMode);
  const [hitlOpen, setHitlOpen] = useState(false);

  if (!activeInstance) return null;

  const isSuspended = activeInstance.status === "suspended";
  const canPause =
    isExecuting &&
    (activeInstance.status === "running" || activeInstance.status === "queued");
  const canResumePaused = activeInstance.status === "paused";
  const canStop =
    !!currentWorkflow &&
    isExecuting &&
    (activeInstance.status === "running" ||
      activeInstance.status === "queued" ||
      activeInstance.status === "paused");
  const canReplay =
    !!currentWorkflow &&
    !isExecuting &&
    ["completed", "failed", "cancelled", "paused"].includes(activeInstance.status);
  const Icon = STATUS_ICON[activeInstance.status] ?? CircleDot;
  const color = STATUS_COLOR[activeInstance.status] ?? "text-muted-foreground";

  const handleReviewResume = async () => {
    if (!currentWorkflow) return;
    await fetchInstanceContext(currentWorkflow.id, activeInstance.id);
    setHitlOpen(true);
  };

  return (
    <div className="absolute bottom-0 left-0 right-0 bg-card border-t shadow-lg z-10 max-h-[45%] flex flex-col">
      <div className="flex items-center gap-2 px-4 py-2 shrink-0">
        <Icon
          className={`h-4 w-4 ${color} ${activeInstance.status === "running" ? "animate-spin" : ""}`}
        />
        <span className="text-sm font-medium">Execution</span>
        <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${color}`}>
          {activeInstance.status}
        </Badge>
        {isExecuting && (
          <span className="text-[10px] text-muted-foreground">
            streaming…
          </span>
        )}
        <div className="flex-1" />
        {canPause && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px] gap-1 text-cyan-700 border-cyan-300 hover:bg-cyan-50 dark:text-cyan-300 dark:border-cyan-800 dark:hover:bg-cyan-950/40"
            title="Pause after the current node finishes (same as Stop timing, but you can resume)"
            onClick={() => pauseInstance(currentWorkflow!.id, activeInstance.id)}
          >
            <PauseCircle className="h-3 w-3" />
            Pause
          </Button>
        )}
        {canResumePaused && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px] gap-1 text-cyan-700 border-cyan-300 hover:bg-cyan-50"
            title="Continue from this paused run"
            onClick={() => resumePausedInstance(currentWorkflow!.id, activeInstance.id)}
          >
            <Play className="h-3 w-3" />
            Resume
          </Button>
        )}
        {canStop && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px] gap-1 text-orange-700 border-orange-300 hover:bg-orange-50 dark:text-orange-300 dark:border-orange-800 dark:hover:bg-orange-950/40"
            title={
              activeInstance.status === "paused"
                ? "Discard this run (cancelled)"
                : "Stop after the current node finishes (does not interrupt mid-node)"
            }
            onClick={() => cancelInstance(currentWorkflow!.id, activeInstance.id)}
          >
            <StopCircle className="h-3 w-3" />
            Stop
          </Button>
        )}
        {isSuspended && currentWorkflow && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px] gap-1 text-yellow-700 border-yellow-300 hover:bg-yellow-50"
            onClick={handleReviewResume}
          >
            <ClipboardCheck className="h-3 w-3" />
            Review &amp; Resume
          </Button>
        )}
        {canReplay && currentWorkflow && !isDebugMode && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px] gap-1 text-indigo-700 border-indigo-300 hover:bg-indigo-50 dark:text-indigo-300 dark:border-indigo-800 dark:hover:bg-indigo-950/40"
            title="Step through saved checkpoints on the canvas"
            onClick={() => void enterDebugMode()}
          >
            <Bug className="h-3 w-3" />
            Debug
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0"
          onClick={clearExecution}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {currentWorkflow && instanceContext && (
        <HITLResumeDialog
          open={hitlOpen}
          onClose={() => setHitlOpen(false)}
          workflowId={currentWorkflow.id}
          instanceId={activeInstance.id}
          context={instanceContext}
        />
      )}
      <Separator />
      {isDebugMode && <DebugReplayBar />}
      <ScrollArea className="flex-1 px-4 py-2">
        <div className="space-y-1.5">
          {activeInstance.logs.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              {isExecuting ? "Waiting for execution logs..." : "No execution logs yet."}
            </p>
          ) : (
            activeInstance.logs.map((log) => (
              <LogEntry
                key={log.id}
                log={log}
                streamingText={streamingTokens[log.node_id]}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
