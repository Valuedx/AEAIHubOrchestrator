/**
 * HITLResumeDialog
 *
 * Shown when a workflow is suspended at a Human Approval node.
 * Lets the operator:
 *   1. Read the approvalMessage from the suspended node config
 *   2. Inspect the current execution context (read-only)
 *   3. Optionally supply a context patch (JSON) to override specific
 *      context keys before the workflow resumes
 *   4. Approve (resume) or Reject the workflow
 */

import { useState, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { AlertTriangle } from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";
import type { InstanceContextOut } from "@/lib/api";

interface HITLResumeDialogProps {
  open: boolean;
  onClose: () => void;
  workflowId: string;
  instanceId: string;
  context: InstanceContextOut;
}

export function HITLResumeDialog({
  open,
  onClose,
  workflowId,
  instanceId,
  context,
}: HITLResumeDialogProps) {
  const [patchText, setPatchText] = useState("{}");
  const [patchError, setPatchError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const resumeInstance = useWorkflowStore((s) => s.resumeInstance);

  const handleAction = useCallback(
    async (rejected: boolean) => {
      let patch: Record<string, unknown> | undefined;

      if (!rejected) {
        try {
          const parsed = JSON.parse(patchText);
          if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
            setPatchError("Context patch must be a JSON object, not an array or primitive.");
            return;
          }
          patch = Object.keys(parsed).length > 0 ? parsed : undefined;
          setPatchError(null);
        } catch {
          setPatchError("Invalid JSON — fix syntax before approving.");
          return;
        }
      }

      setLoading(true);
      try {
        await resumeInstance(
          workflowId,
          instanceId,
          rejected ? { rejected: true } : { approved: true },
          patch,
        );
        onClose();
      } finally {
        setLoading(false);
      }
    },
    [workflowId, instanceId, patchText, resumeInstance, onClose],
  );

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl w-full max-h-[85vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <DialogTitle>Human Approval Required</DialogTitle>
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 text-yellow-700 border-yellow-300"
            >
              suspended
            </Badge>
          </div>
          {context.current_node_id && (
            <p className="text-[10px] text-muted-foreground font-mono mt-0.5">
              Paused at: {context.current_node_id}
            </p>
          )}
        </DialogHeader>

        <div className="space-y-4 flex-1 overflow-y-auto min-h-0">
          {/* Approval message from the node config */}
          {context.approval_message ? (
            <div className="flex items-start gap-2 bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 rounded-md px-3 py-2">
              <AlertTriangle className="h-4 w-4 text-yellow-600 shrink-0 mt-0.5" />
              <p className="text-sm text-yellow-800 dark:text-yellow-200">
                {context.approval_message}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              A node requires human approval before the workflow can continue.
            </p>
          )}

          {/* Current context — read-only */}
          <div>
            <p className="text-xs font-medium mb-1">Current Execution Context</p>
            <ScrollArea className="max-h-48 border rounded-md bg-muted/40">
              <pre className="text-xs font-mono p-3 whitespace-pre-wrap break-all">
                {JSON.stringify(context.context_json, null, 2)}
              </pre>
            </ScrollArea>
          </div>

          <Separator />

          {/* Context patch editor */}
          <div>
            <Label className="text-xs font-medium">
              Context Patch{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <p className="text-[10px] text-muted-foreground mb-1.5 leading-relaxed">
              JSON object whose keys will be merged into the execution context
              before resuming. Leave as{" "}
              <code className="font-mono bg-muted px-0.5 rounded">{"{}"}</code> to
              resume without changes.
            </p>
            <Textarea
              className="font-mono text-xs min-h-[72px] resize-none"
              value={patchText}
              onChange={(e) => {
                setPatchText(e.target.value);
                setPatchError(null);
              }}
              placeholder='{"node_3": {"score": 0.95}}'
              spellCheck={false}
            />
            {patchError && (
              <p className="text-xs text-red-500 mt-1">{patchError}</p>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="outline"
            onClick={() => handleAction(true)}
            disabled={loading}
            className="text-red-600 border-red-200 hover:bg-red-50 dark:hover:bg-red-950/30"
          >
            Reject
          </Button>
          <Button
            onClick={() => handleAction(false)}
            disabled={loading}
          >
            {loading ? "Resuming…" : "Approve & Resume"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
