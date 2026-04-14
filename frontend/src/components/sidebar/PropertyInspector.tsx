import { useState, useCallback } from "react";
import { useFlowStore } from "@/store/flowStore";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { X, Copy, Check } from "lucide-react";
import type { AgenticNodeData } from "@/types/nodes";
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
    <div className="flex flex-col w-72 h-full min-h-0 border-l bg-sidebar overflow-hidden">
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
      <ScrollArea className="flex-1 px-4 py-3">
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
