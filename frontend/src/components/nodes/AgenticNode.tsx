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
  GitFork,
  GitMerge,
  Route,
  History,
  Save,
  MessageSquare,
  AlertCircle,
  AlertTriangle,
  Pin,
  RefreshCw,
  RotateCw,
  Database,
  Code2,
  Bell,
  Target,
  ListFilter,
  Layers,
  Network,
  Sparkles,
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
  "git-fork": GitFork,
  "git-merge": GitMerge,
  route: Route,
  history: History,
  save: Save,
  "message-square": MessageSquare,
  "refresh-cw": RefreshCw,
  "rotate-cw": RotateCw,
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
  // NODES-01.a — Switch renders one handle per case + a default handle.
  const isSwitch = nodeCategory === "logic" && label === "Switch";
  const switchCases = (isSwitch
    ? ((config?.cases as Array<{ value?: string; label?: string }> | undefined) ?? [])
    : []
  ).filter((c) => c && typeof c.value === "string" && c.value.length > 0);
  const switchDefaultLabel =
    (isSwitch && typeof config?.defaultLabel === "string" && config.defaultLabel) ||
    "Default";

  // Design-time validation indicators
  const { errorIds, warningIds } = useNodeValidation();
  const hasError = errorIds.has(id);
  const hasWarning = !hasError && warningIds.has(id);

  // COPILOT-02.ii.b — diff annotation set by flowStore.setCopilotPreview
  // when the canvas is in preview mode. "added" = this node is new in
  // the draft (not on the base workflow); "modified" = same id but
  // different config/label. The ring + corner-badge make both
  // obvious without hiding category styling.
  const diffStatus = (nodeData as Record<string, unknown>).__copilotDiff as
    | "added"
    | "modified"
    | "unchanged"
    | undefined;
  const isCopilotAdded = diffStatus === "added";
  const isCopilotModified = diffStatus === "modified";

  // NODES-01.a — Switch card needs enough height for N+1 evenly-spaced
  // handles (one per case + default). Each handle gets ~18px of vertical
  // breathing room so labels don't overlap. Capped at 24 cases (≈ 450px).
  const switchMinHeight = isSwitch
    ? Math.max(120, 80 + (switchCases.length + 1) * 18)
    : undefined;

  return (
    <Card
      style={switchMinHeight ? { minHeight: `${switchMinHeight}px` } : undefined}
      className={cn(
        "min-w-[180px] max-w-[220px] border-2 shadow-md transition-shadow relative",
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
        // COPILOT-02.ii.b — diff rings. Added = dashed amber;
        // modified = solid amber. Only paint when no higher-priority
        // ring is already on (selected / replay / validation).
        !selected && !isReplayCursor && !hasError && !hasWarning &&
          isCopilotAdded && "ring-2 ring-dashed ring-amber-400 shadow-lg",
        !selected && !isReplayCursor && !hasError && !hasWarning &&
          isCopilotModified && "ring-2 ring-amber-400",
      )}
    >
      {hasInput && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-3 !h-3 !bg-muted-foreground !border-2 !border-background"
        />
      )}

      {(isCopilotAdded || isCopilotModified) && (
        <span
          className={cn(
            "absolute -top-2 -right-2 inline-flex items-center gap-0.5 rounded-full border px-1.5 py-0.5 text-[9px] font-medium shadow-sm",
            isCopilotAdded
              ? "bg-amber-50 border-amber-400 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300"
              : "bg-amber-100 border-amber-400 text-amber-800 dark:bg-amber-950/60 dark:text-amber-200",
          )}
          title={
            isCopilotAdded
              ? "Copilot added this node"
              : "Copilot modified this node"
          }
        >
          <Sparkles className="h-2.5 w-2.5" />
          {isCopilotAdded ? "new" : "edit"}
        </span>
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
          {label === "While" && config?.maxIterations != null && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              ⟳ ≤{String(config.maxIterations)}×
            </Badge>
          )}
          {isSwitch && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              {switchCases.length} case{switchCases.length === 1 ? "" : "s"} + default
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
        {label === "While" && config?.condition != null && config.condition !== "" && (
          <p
            className="text-[10px] font-mono text-muted-foreground truncate mt-1 leading-tight"
            title={String(config.condition)}
          >
            ⟳ while {String(config.condition)}
          </p>
        )}
        {isSwitch && config?.expression != null && config.expression !== "" && (
          <p
            className="text-[10px] font-mono text-muted-foreground truncate mt-1 leading-tight"
            title={String(config.expression)}
          >
            ⑂ {String(config.expression)}
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
      ) : isSwitch ? (
        <SwitchHandles
          cases={switchCases as Array<{ value: string; label?: string }>}
          defaultLabel={switchDefaultLabel}
        />
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


/**
 * NODES-01.a — N+1 output handles for a Switch node, evenly spaced down
 * the right edge. Each handle's id is the case ``value`` (the matcher
 * string) so edges hook up to ``sourceHandle === value`` and the
 * dag_runner's branch-pruning path fires unchanged.
 *
 * Visual layout:
 *   - Case handles coloured teal (positive-match semantics).
 *   - Default handle coloured amber so the fallback path is visually
 *     distinct. Always present — authors can ignore it if exhaustive.
 *   - Case labels painted to the right of each handle; fall back to
 *     the case value when no label is set.
 *   - Handles start at 30% of node height and distribute evenly; the
 *     node min-height grows with case count so stacked labels never
 *     overlap.
 */
function SwitchHandles({
  cases,
  defaultLabel,
}: {
  cases: Array<{ value: string; label?: string }>;
  defaultLabel: string;
}) {
  const total = cases.length + 1; // +1 for the always-present default
  // Spread handles from 30% to 90% of the node height. Safe-zone
  // chosen so labels don't collide with the header or bottom edge.
  const start = 30;
  const end = 90;
  const step = total > 1 ? (end - start) / (total - 1) : 0;

  return (
    <>
      {cases.map((c, i) => {
        const top = start + i * step;
        return (
          <div key={c.value}>
            <div
              className="absolute right-[-4px] text-[8px] font-semibold text-teal-700 dark:text-teal-300 whitespace-nowrap"
              style={{
                top: `${top}%`,
                transform: "translateX(100%) translateY(-50%)",
                paddingLeft: 6,
              }}
              title={c.label || c.value}
            >
              {c.label || c.value}
            </div>
            <Handle
              type="source"
              position={Position.Right}
              id={c.value}
              className="!w-3 !h-3 !bg-teal-500 !border-2 !border-background"
              style={{ top: `${top}%` }}
            />
          </div>
        );
      })}
      <div
        className="absolute right-[-4px] text-[8px] font-semibold text-amber-700 dark:text-amber-300 whitespace-nowrap"
        style={{
          top: `${start + cases.length * step}%`,
          transform: "translateX(100%) translateY(-50%)",
          paddingLeft: 6,
        }}
        title={`${defaultLabel} (unmatched values)`}
      >
        {defaultLabel}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        id="default"
        className="!w-3 !h-3 !bg-amber-500 !border-2 !border-background"
        style={{ top: `${start + cases.length * step}%` }}
      />
    </>
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
