/**
 * ValidationDialog — shown before workflow execution when validation errors exist.
 *
 * - Errors (red)   → block execution; user must fix before running
 * - Warnings (yellow) → execution allowed after confirmation
 */

import { AlertTriangle, XCircle, CheckCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import type { ValidationError } from "@/lib/validateWorkflow";

interface ValidationDialogProps {
  open: boolean;
  errors: ValidationError[];
  onClose: () => void;
  onRunAnyway: () => void;
}

export function ValidationDialog({
  open,
  errors,
  onClose,
  onRunAnyway,
}: ValidationDialogProps) {
  const hardErrors = errors.filter((e) => e.severity === "error");
  const warnings   = errors.filter((e) => e.severity === "warning");
  const canRunAnyway = hardErrors.length === 0 && warnings.length > 0;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {hardErrors.length > 0 ? (
              <XCircle className="h-5 w-5 text-red-500" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
            )}
            Workflow Validation
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="flex-1 min-h-0 pr-1">
          <div className="space-y-2">
            {hardErrors.map((err, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/30 px-3 py-2"
              >
                <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  {err.nodeId && (
                    <Badge variant="outline" className="text-[9px] px-1 py-0 mr-1.5 font-mono text-red-600 border-red-300">
                      {err.nodeId}
                    </Badge>
                  )}
                  <span className="text-xs text-red-700 dark:text-red-300">{err.message}</span>
                </div>
              </div>
            ))}

            {warnings.map((warn, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-md border border-yellow-200 bg-yellow-50 dark:border-yellow-900 dark:bg-yellow-950/30 px-3 py-2"
              >
                <AlertTriangle className="h-3.5 w-3.5 text-yellow-500 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  {warn.nodeId && (
                    <Badge variant="outline" className="text-[9px] px-1 py-0 mr-1.5 font-mono text-yellow-600 border-yellow-300">
                      {warn.nodeId}
                    </Badge>
                  )}
                  <span className="text-xs text-yellow-700 dark:text-yellow-300">{warn.message}</span>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            Fix Issues
          </Button>
          {canRunAnyway && (
            <Button
              variant="default"
              size="sm"
              onClick={onRunAnyway}
              className="gap-1.5"
            >
              <CheckCircle className="h-3.5 w-3.5" />
              Run Anyway
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
