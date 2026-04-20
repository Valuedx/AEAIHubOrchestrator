import { useState, useCallback } from "react";
import { useFlowStore } from "@/store/flowStore";
import { useWorkflowStore } from "@/store/workflowStore";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  X,
  Copy,
  Check,
  Pin,
  PinOff,
  Play,
  Loader2,
  CircleAlert,
} from "lucide-react";
import type { AgenticNodeData } from "@/types/nodes";
import { api, type TestNodeResponse } from "@/lib/api";
import { getRegistryNodeType, getConfigSchema } from "@/lib/registry";
import { DynamicConfigForm } from "@/components/sidebar/DynamicConfigForm";

export function PropertyInspector() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const nodes = useFlowStore((s) => s.nodes);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const selectNode = useFlowStore((s) => s.selectNode);
  const deleteNode = useFlowStore((s) => s.deleteNode);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);

  const [idCopied, setIdCopied] = useState(false);
  const handleCopyId = useCallback(() => {
    if (!selectedNode) return;
    navigator.clipboard.writeText(selectedNode.id).then(() => {
      setIdCopied(true);
      setTimeout(() => setIdCopied(false), 2000);
    });
  }, [selectedNode]);

  if (!selectedNode) {
    return (
      <div className="flex flex-col items-center justify-center w-72 border-l bg-sidebar text-muted-foreground p-6">
        <p className="text-sm text-center">
          Select a node on the canvas to inspect its properties.
        </p>
      </div>
    );
  }

  const data = selectedNode.data as AgenticNodeData;
  const onUpdate = (partial: Partial<AgenticNodeData>) =>
    updateNodeData(selectedNode.id, partial);

  const registryType = getRegistryNodeType(data.label);
  const schema = getConfigSchema(data.label);

  return (
    <div className="flex flex-col w-72 border-l bg-sidebar min-h-0">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold">Properties</h2>
        <button
          onClick={() => selectNode(null)}
          className="p-1 rounded-md hover:bg-accent transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 min-h-0 px-4 py-3">
        <div className="space-y-4">
          {/* Node ID chip */}
          <div className="flex items-center gap-1.5 rounded-md bg-muted px-2.5 py-1.5">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide shrink-0">
              ID
            </span>
            <code className="flex-1 text-xs font-mono text-foreground truncate">
              {selectedNode.id}
            </code>
            <button
              onClick={handleCopyId}
              title={idCopied ? "Copied!" : "Copy node ID"}
              className="p-0.5 rounded hover:bg-accent transition-colors text-muted-foreground hover:text-foreground shrink-0"
            >
              {idCopied ? (
                <Check className="h-3 w-3 text-green-500" />
              ) : (
                <Copy className="h-3 w-3" />
              )}
            </button>
          </div>

          <div className="space-y-2">
            <Label htmlFor="nodeDisplayName">Display name (canvas)</Label>
            <Input
              id="nodeDisplayName"
              value={typeof data.displayName === "string" ? data.displayName : ""}
              placeholder={data.label}
              onChange={(e) =>
                onUpdate({ displayName: e.target.value || undefined })
              }
            />
            <p className="text-[10px] text-muted-foreground">
              Shown on the graph. Leave empty to use the engine type below.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="nodeLabel">Engine type (registry)</Label>
            <Input
              id="nodeLabel"
              value={data.label}
              onChange={(e) => onUpdate({ label: e.target.value })}
            />
            <p className="text-[10px] text-muted-foreground">
              Must match a palette node type for properties and execution.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Badge variant="outline">{data.nodeCategory}</Badge>
            <Badge variant="secondary">{data.status || "idle"}</Badge>
          </div>

          <Separator />

          {schema && registryType ? (
            <DynamicConfigForm
              nodeType={registryType.type}
              schema={schema}
              config={(data.config as Record<string, unknown>) ?? {}}
              onUpdate={onUpdate}
            />
          ) : (
            <p className="text-xs text-muted-foreground">
              No configurable properties.
            </p>
          )}

          <Separator />

          <TestSection nodeId={selectedNode.id} />

          <Separator />

          <PinSection nodeId={selectedNode.id} data={data} />

          <Separator />

          <Button
            variant="destructive"
            size="sm"
            className="w-full"
            onClick={() => {
              deleteNode(selectedNode.id);
            }}
          >
            Delete Node
          </Button>
        </div>
      </ScrollArea>
    </div>
  );
}


/**
 * DV-02 — Test single node section.
 *
 * Runs the selected node in isolation via the ``POST .../test``
 * endpoint. Upstream pinned outputs populate the synthetic context,
 * so this pairs naturally with DV-01: pin the predecessors, then
 * iterate on the current node's config without re-running the whole
 * DAG. Result is shown inline — elapsed time badge + JSON output OR
 * error string.
 */
function TestSection({ nodeId }: { nodeId: string }) {
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestNodeResponse | null>(null);

  const canTest = !!currentWorkflow;

  const handleTest = async () => {
    if (!currentWorkflow) return;
    setBusy(true);
    setResult(null);
    try {
      const res = await api.testNode(currentWorkflow.id, nodeId);
      setResult(res);
    } catch (e) {
      // Only reached on transport-level failures (4xx on unknown node,
      // network error, etc.). Handler raises are already caught server-
      // side and returned as ``error`` in a 200 body.
      setResult({
        output: null,
        elapsed_ms: 0,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Play className="h-3.5 w-3.5" />
        <span className="font-medium">Test this node</span>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Runs just this node using upstream pinned outputs. No execution
        log or workflow instance is created.
      </p>
      <Button
        variant="outline"
        size="sm"
        className="w-full gap-1.5"
        onClick={handleTest}
        disabled={!canTest || busy}
        title={
          canTest
            ? "Execute only this node with current config"
            : "Save the workflow first"
        }
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Play className="h-3.5 w-3.5" />
        )}
        {busy ? "Running…" : "Test node"}
      </Button>

      {result && !busy && <TestResultPanel result={result} />}
    </div>
  );
}

function TestResultPanel({ result }: { result: TestNodeResponse }) {
  if (result.error) {
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 text-[10px] text-red-600 dark:text-red-400">
          <CircleAlert className="h-3 w-3" />
          <span className="font-medium">Error</span>
          <span className="text-muted-foreground ml-auto">
            {result.elapsed_ms} ms
          </span>
        </div>
        <pre className="text-xs bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 rounded p-2 font-mono whitespace-pre-wrap break-words">
          {result.error}
        </pre>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-[10px] text-green-600 dark:text-green-400">
        <Check className="h-3 w-3" />
        <span className="font-medium">OK</span>
        <span className="text-muted-foreground ml-auto">
          {result.elapsed_ms} ms
        </span>
      </div>
      <pre className="text-xs bg-muted rounded p-2 max-h-56 overflow-y-auto font-mono whitespace-pre-wrap break-words">
        {JSON.stringify(result.output, null, 2)}
      </pre>
    </div>
  );
}


/**
 * DV-01 — Pin / Unpin section.
 *
 * Three display states:
 *   * node has ``pinnedOutput``  → unpin button + preview of pinned keys
 *   * node has no pin + has a completed log in activeInstance → pin button
 *   * node has no pin + no completed log               → disabled pin button
 */
function PinSection({
  nodeId,
  data,
}: {
  nodeId: string;
  data: AgenticNodeData;
}) {
  const activeInstance = useWorkflowStore((s) => s.activeInstance);
  const pinNode = useWorkflowStore((s) => s.pinNode);
  const unpinNode = useWorkflowStore((s) => s.unpinNode);
  const [busy, setBusy] = useState(false);

  const isPinned = !!data.pinnedOutput;
  const hasCompletedLog = !!activeInstance?.logs.some(
    (l) => l.node_id === nodeId && l.status === "completed",
  );

  const handlePin = async () => {
    setBusy(true);
    await pinNode(nodeId);
    setBusy(false);
  };

  const handleUnpin = async () => {
    setBusy(true);
    await unpinNode(nodeId);
    setBusy(false);
  };

  if (isPinned) {
    const keys = Object.keys(data.pinnedOutput ?? {}).filter(
      (k) => !k.startsWith("_"),
    );
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs text-amber-700 dark:text-amber-300">
          <Pin className="h-3.5 w-3.5 fill-amber-500 text-amber-500" />
          <span className="font-medium">Output is pinned</span>
        </div>
        <p className="text-[11px] text-muted-foreground">
          The next runs will short-circuit this node and return the saved
          output without calling the handler. Unpin to re-run live.
        </p>
        {keys.length > 0 && (
          <p className="text-[10px] text-muted-foreground font-mono truncate">
            keys: {keys.join(", ")}
          </p>
        )}
        <Button
          variant="outline"
          size="sm"
          className="w-full gap-1.5"
          onClick={handleUnpin}
          disabled={busy}
        >
          <PinOff className="h-3.5 w-3.5" />
          Unpin output
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Pin className="h-3.5 w-3.5" />
        <span className="font-medium">Pin last output</span>
      </div>
      <p className="text-[11px] text-muted-foreground">
        After a successful run, pin the output to short-circuit this node on
        subsequent tests. Saves LLM / MCP cost during iteration.
      </p>
      <Button
        variant="outline"
        size="sm"
        className="w-full gap-1.5"
        onClick={handlePin}
        disabled={busy || !hasCompletedLog}
        title={
          hasCompletedLog
            ? "Pin the latest completed output for this node"
            : "Run the workflow first to capture an output"
        }
      >
        <Pin className="h-3.5 w-3.5" />
        Pin output
      </Button>
    </div>
  );
}
