import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  Webhook,
  Clock,
  Brain,
  Repeat,
  Wrench,
  Globe,
  UserCheck,
  GitBranch,
  GitMerge,
  Route,
  History,
  Save,
  MessageSquare,
  AlertCircle,
  AlertTriangle,
  Pin,
  RefreshCw,
  Database,
  Code2,
  Bell,
  Target,
  ListFilter,
  Layers,
  Network,
  type LucideIcon,
} from "lucide-react";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { nodeCanvasTitle, type AgenticNodeData, type NodeCategory } from "@/types/nodes";
import { cn } from "@/lib/utils";
import { useNodeValidation } from "@/lib/useNodeValidation";
import { useWorkflowStore } from "@/store/workflowStore";

const ICON_MAP: Record<string, LucideIcon> = {
  webhook: Webhook,
  clock: Clock,
  brain: Brain,
  repeat: Repeat,
  wrench: Wrench,
  globe: Globe,
  "user-check": UserCheck,
  "git-branch": GitBranch,
  "git-merge": GitMerge,
  route: Route,
  history: History,
  save: Save,
  "message-square": MessageSquare,
  "refresh-cw": RefreshCw,
  database: Database,
  code: Code2,
  bell: Bell,
  target: Target,
  "list-filter": ListFilter,
  workflow: Layers,
  network: Network,
  layers: Layers,
};

const CATEGORY_STYLES: Record<
  NodeCategory,
  { border: string; bg: string; badge: string }
> = {
  trigger: {
    border: "border-amber-500/60",
    bg: "bg-amber-50 dark:bg-amber-950/30",
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  },
  agent: {
    border: "border-violet-500/60",
    bg: "bg-violet-50 dark:bg-violet-950/30",
    badge:
      "bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-200",
  },
  action: {
    border: "border-sky-500/60",
    bg: "bg-sky-50 dark:bg-sky-950/30",
    badge: "bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-200",
  },
  logic: {
    border: "border-emerald-500/60",
    bg: "bg-emerald-50 dark:bg-emerald-950/30",
    badge:
      "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
  },
  knowledge: {
    border: "border-teal-500/60",
    bg: "bg-teal-50 dark:bg-teal-950/30",
    badge:
      "bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-200",
  },
  notification: {
    border: "border-rose-500/60",
    bg: "bg-rose-50 dark:bg-rose-950/30",
    badge:
      "bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200",
  },
  nlp: {
    border: "border-indigo-500/60",
    bg: "bg-indigo-50 dark:bg-indigo-950/30",
    badge:
      "bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200",
  },
};

const STATUS_DOT: Record<string, string> = {
  idle: "bg-gray-400",
  running: "bg-blue-500 animate-pulse",
  completed: "bg-green-500",
  failed: "bg-red-500",
  suspended: "bg-yellow-500",
  paused: "bg-cyan-500",
  // Terminal-run, never-reached: Condition-branch-pruned or downstream
  // of a failure. Lower-contrast so it reads as "not relevant to this run".
  skipped: "bg-gray-300 opacity-60",
};

function AgenticNodeComponent({ id, data, selected }: NodeProps) {
  const nodeData = data as unknown as AgenticNodeData;
  const { label, nodeCategory, config, status = "idle" } = nodeData;
  const replayCursorId = useWorkflowStore((s) => {
    if (!s.isDebugMode || s.activeCheckpointIdx == null) return null;
    return s.debugCheckpoints[s.activeCheckpointIdx]?.node_id ?? null;
  });
  const isReplayCursor = replayCursorId === id;
  const styles = CATEGORY_STYLES[nodeCategory];
  const iconName = (config?.icon as string) || getDefaultIcon(nodeCategory);
  const Icon = ICON_MAP[iconName] || Brain;

  const hasInput = nodeCategory !== "trigger";
  const hasOutput = nodeCategory !== "logic" || label !== "Merge";
  const isCondition = nodeCategory === "logic" && label === "Condition";

  // Design-time validation indicators
  const { errorIds, warningIds } = useNodeValidation();
  const hasError = errorIds.has(id);
  const hasWarning = !hasError && warningIds.has(id);

  return (
    <Card
      className={cn(
        "min-w-[180px] max-w-[220px] border-2 shadow-md transition-shadow",
        styles.border,
        styles.bg,
        // Selection ring takes highest priority
        selected && "ring-2 ring-primary shadow-lg",
        // Checkpoint replay cursor (debug mode)
        !selected && isReplayCursor && "ring-2 ring-indigo-500 shadow-lg",
        // Error ring when not selected
        !selected && hasError && "ring-2 ring-red-500/70",
        // Warning ring when not selected and no error
        !selected && hasWarning && "ring-2 ring-yellow-500/60",
      )}
    >
      {hasInput && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-3 !h-3 !bg-muted-foreground !border-2 !border-background"
        />
      )}

      <CardHeader className="p-3 pb-2">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-md p-1.5", styles.badge)}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="flex-1 min-w-0">
            <CardTitle className="text-sm font-medium truncate" title={label}>
              {nodeCanvasTitle(nodeData)}
            </CardTitle>
          </div>
          {nodeData.pinnedOutput && (
            <span
              className="shrink-0"
              title="Output pinned — dispatch is short-circuited until unpinned"
            >
              <Pin className="h-3.5 w-3.5 fill-amber-500 text-amber-500" />
            </span>
          )}
          {hasError && (
            <span className="shrink-0" title="This node has configuration errors">
              <AlertCircle className="h-3.5 w-3.5 text-red-500" />
            </span>
          )}
          {hasWarning && (
            <span className="shrink-0" title="This node is not connected to a trigger">
              <AlertTriangle className="h-3.5 w-3.5 text-yellow-500" />
            </span>
          )}
          {!hasError && !hasWarning && (
            <span className={cn("h-2 w-2 rounded-full shrink-0", STATUS_DOT[status])} />
          )}
        </div>
        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
          <Badge variant="outline" className={cn("text-[10px] px-1.5 py-0", styles.badge)}>
            {nodeCategory}
          </Badge>
          {nodeCategory === "agent" && config?.model != null && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {String(config.model)}
            </Badge>
          )}
          {label === "Merge" && config?.strategy != null && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {String(config.strategy)}
            </Badge>
          )}
          {label === "Loop" && config?.maxIterations != null && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              ≤{String(config.maxIterations)}×
            </Badge>
          )}
          {label === "Sub-Workflow" && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {config?.versionPolicy === "pinned" ? `v${config.pinnedVersion ?? "?"}` : "latest"}
            </Badge>
          )}
        </div>
        {label === "ForEach" && config?.arrayExpression != null && config.arrayExpression !== "" && (
          <p
            className="text-[10px] font-mono text-muted-foreground truncate mt-1 leading-tight"
            title={String(config.arrayExpression)}
          >
            ↻ {String(config.arrayExpression)}
          </p>
        )}
        {label === "Loop" && config?.continueExpression != null && config.continueExpression !== "" && (
          <p
            className="text-[10px] font-mono text-muted-foreground truncate mt-1 leading-tight"
            title={String(config.continueExpression)}
          >
            ⟳ {String(config.continueExpression)}
          </p>
        )}
      </CardHeader>

      {isCondition ? (
        <>
          <div className="absolute right-[-4px] text-[8px] font-bold text-green-600 dark:text-green-400" style={{ top: "25%", transform: "translateX(100%) translateY(-50%)", paddingLeft: 6 }}>
            Yes
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="true"
            className="!w-3 !h-3 !bg-green-500 !border-2 !border-background"
            style={{ top: "35%" }}
          />
          <div className="absolute right-[-4px] text-[8px] font-bold text-red-600 dark:text-red-400" style={{ top: "57%", transform: "translateX(100%) translateY(-50%)", paddingLeft: 6 }}>
            No
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="false"
            className="!w-3 !h-3 !bg-red-500 !border-2 !border-background"
            style={{ top: "65%" }}
          />
        </>
      ) : (
        hasOutput && (
          <Handle
            type="source"
            position={Position.Right}
            className="!w-3 !h-3 !bg-muted-foreground !border-2 !border-background"
          />
        )
      )}
    </Card>
  );
}

function getDefaultIcon(category: NodeCategory): string {
  switch (category) {
    case "trigger":
      return "webhook";
    case "agent":
      return "brain";
    case "action":
      return "wrench";
    case "logic":
      return "git-branch";
    case "knowledge":
      return "database";
    case "notification":
      return "bell";
    case "nlp":
      return "target";
    default:
      return "brain";
  }
}

export const AgenticNode = memo(AgenticNodeComponent);
