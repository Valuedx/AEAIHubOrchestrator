/**
 * HITLResumeDialog (HITL-01.a)
 *
 * Shown when a workflow is suspended at a Human Approval node.
 *
 * UX hierarchy, top → bottom:
 *   1. Approval message — big yellow AlertTriangle banner so the
 *      operator knows immediately why the workflow is paused.
 *   2. "Approving as" — claimed identity input, backed by
 *      localStorage so repeat approvers don't retype their name
 *      every time. First-time: blue info banner explains that the
 *      name is stored locally (no SSO yet).
 *   3. Reason — required when rejecting (forces intentionality),
 *      optional when approving.
 *   4. Advanced — collapsed accordion containing the context
 *      JSON + the patch editor. Most approvals don't need these;
 *      collapsing keeps the dialog's visual weight low.
 *   5. Footer — Reject (destructive outline) + Approve (primary).
 *      The Approve button is the default so Enter works for the
 *      happy path.
 *
 * Every submission writes an approval_audit_log row on the
 * backend, so this dialog is also the single source of capture
 * for the compliance story.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  CircleX,
  Info,
  UserCircle2,
} from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";
import { cn } from "@/lib/utils";
import type { InstanceContextOut } from "@/lib/api";


interface HITLResumeDialogProps {
  open: boolean;
  onClose: () => void;
  workflowId: string;
  instanceId: string;
  context: InstanceContextOut;
}


// localStorage key for the claimed-identity pre-fill. Mirror of
// settings.preferredApprover — kept deliberately vanilla (no store
// indirection) because this value is per-browser, not per-tenant
// and not something the user manages through Settings.
const APPROVER_STORAGE_KEY = "aeai.hitl.approver";


export function HITLResumeDialog({
  open,
  onClose,
  workflowId,
  instanceId,
  context,
}: HITLResumeDialogProps) {
  const resumeInstance = useWorkflowStore((s) => s.resumeInstance);

  const [approver, setApprover] = useState("");
  const [reason, setReason] = useState("");
  const [patchText, setPatchText] = useState("{}");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [approverError, setApproverError] = useState<string | null>(null);
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [loading, setLoading] = useState<"approved" | "rejected" | null>(null);

  // Pre-fill the approver from localStorage on open. First-time
  // users see an empty input + the blue "only you see it" banner.
  useEffect(() => {
    if (!open) return;
    try {
      const stored = window.localStorage.getItem(APPROVER_STORAGE_KEY) ?? "";
      setApprover(stored);
    } catch {
      // localStorage unavailable (incognito quota, etc.) — fall back
      // to empty. The approver will have to retype every time in
      // that browser, which is the least-bad option.
      setApprover("");
    }
    setReason("");
    setPatchText("{}");
    setAdvancedOpen(false);
    setPatchError(null);
    setApproverError(null);
    setReasonError(null);
    setLoading(null);
  }, [open]);

  const isReturningApprover = useMemo(() => {
    try {
      return Boolean(window.localStorage.getItem(APPROVER_STORAGE_KEY));
    } catch {
      return false;
    }
  }, []);

  const handleAction = useCallback(
    async (decision: "approved" | "rejected") => {
      // Approver required — "anonymous" is a legal backend default
      // but we'd rather nudge the operator to claim their name up
      // front than have the audit log fill with anonymous rows.
      const trimmedApprover = approver.trim();
      if (!trimmedApprover) {
        setApproverError("Enter your name or email so the audit log knows who approved this.");
        return;
      }
      setApproverError(null);

      // Reason required on reject; this is where "forces
      // intentionality" matters — a rejection with no reason
      // leaves the next approver with no context.
      const trimmedReason = reason.trim();
      if (decision === "rejected" && !trimmedReason) {
        setReasonError("Rejecting without a reason leaves the next approver guessing. Add a short note.");
        return;
      }
      setReasonError(null);

      let patch: Record<string, unknown> | undefined;
      if (decision === "approved") {
        try {
          const parsed = JSON.parse(patchText);
          if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
            setPatchError("Context patch must be a JSON object, not an array or primitive.");
            setAdvancedOpen(true);
            return;
          }
          patch = Object.keys(parsed).length > 0 ? parsed : undefined;
          setPatchError(null);
        } catch {
          setPatchError("Invalid JSON — fix syntax before approving.");
          setAdvancedOpen(true);
          return;
        }
      }

      // Persist the approver so next time's dialog pre-fills.
      try {
        window.localStorage.setItem(APPROVER_STORAGE_KEY, trimmedApprover);
      } catch {
        /* localStorage unavailable — non-fatal, just skip caching */
      }

      setLoading(decision);
      try {
        await resumeInstance(
          workflowId,
          instanceId,
          decision === "approved" ? { approved: true } : { rejected: true },
          patch,
          {
            approver: trimmedApprover,
            decision,
            reason: trimmedReason || undefined,
          },
        );
        onClose();
      } finally {
        setLoading(null);
      }
    },
    [approver, reason, patchText, workflowId, instanceId, resumeInstance, onClose],
  );

  const disabled = loading !== null;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !disabled && onClose()}>
      <DialogContent className="max-w-2xl w-full max-h-[88vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <DialogTitle>Human approval required</DialogTitle>
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 text-amber-700 border-amber-300 dark:text-amber-300 dark:border-amber-800"
            >
              suspended
            </Badge>
          </div>
          {context.current_node_id && (
            <p className="text-[11px] text-muted-foreground font-mono mt-1">
              Paused at node{" "}
              <span className="bg-muted px-1 rounded">{context.current_node_id}</span>
            </p>
          )}
        </DialogHeader>

        <div className="space-y-4 flex-1 overflow-y-auto min-h-0 pr-1">
          {/* Approval message — the most visible element; if the
              node author wrote one, we highlight it clearly. */}
          {context.approval_message ? (
            <div className="flex items-start gap-2.5 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md px-3 py-2.5">
              <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
              <p className="text-sm text-amber-900 dark:text-amber-100 leading-relaxed">
                {context.approval_message}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              A node is waiting for a human decision before the workflow can continue.
            </p>
          )}

          {/* Approver identity — localStorage-backed so repeat
              approvers don't retype. Compact visual: single input
              with the stored-locally hint only on first use. */}
          <div>
            <Label
              htmlFor="hitl-approver"
              className="text-xs font-medium flex items-center gap-1.5"
            >
              <UserCircle2 className="h-3.5 w-3.5" />
              Approving as
            </Label>
            <Input
              id="hitl-approver"
              className="mt-1.5 text-sm"
              placeholder="you@acme.example"
              value={approver}
              onChange={(e) => {
                setApprover(e.target.value);
                setApproverError(null);
              }}
              disabled={disabled}
              autoFocus={!isReturningApprover}
            />
            {approverError ? (
              <p className="text-xs text-destructive mt-1">{approverError}</p>
            ) : !isReturningApprover ? (
              <div className="flex items-start gap-1.5 mt-1.5 text-[11px] text-muted-foreground">
                <Info className="h-3 w-3 shrink-0 mt-0.5" />
                <span>
                  Stored locally so next time's dialog pre-fills. Single
                  sign-on lands in a later slice — treat your entry as
                  attested, not cryptographically verified.
                </span>
              </div>
            ) : null}
          </div>

          {/* Reason — required on reject, optional on approve. The
              placeholder shifts when the user is clearly rejecting
              so the cue matches the action. */}
          <div>
            <Label htmlFor="hitl-reason" className="text-xs font-medium">
              Reason{" "}
              <span className="font-normal text-muted-foreground">
                (required to reject)
              </span>
            </Label>
            <Textarea
              id="hitl-reason"
              className="mt-1.5 text-sm min-h-[60px] resize-none"
              placeholder="Short note — saved to the audit log."
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                setReasonError(null);
              }}
              disabled={disabled}
              maxLength={2000}
            />
            {reasonError && (
              <p className="text-xs text-destructive mt-1">{reasonError}</p>
            )}
          </div>

          {/* Advanced — collapsed by default. Shows the context
              snapshot + the patch editor for power users. Most
              approvals never need this. */}
          <details
            open={advancedOpen}
            onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
            className="group rounded-md border"
          >
            <summary className="cursor-pointer list-none px-3 py-2 text-xs font-medium flex items-center justify-between select-none hover:bg-accent/50">
              <span>Advanced — context + patch</span>
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 transition-transform",
                  advancedOpen && "rotate-180",
                )}
              />
            </summary>
            <div className="border-t p-3 space-y-3">
              <div>
                <p className="text-[11px] text-muted-foreground mb-1">
                  Current execution context (read-only):
                </p>
                <ScrollArea className="max-h-40 border rounded-md bg-muted/40">
                  <pre className="text-[11px] font-mono p-2.5 whitespace-pre-wrap break-all">
                    {JSON.stringify(context.context_json, null, 2)}
                  </pre>
                </ScrollArea>
              </div>
              <div>
                <Label className="text-xs font-medium">
                  Context patch{" "}
                  <span className="font-normal text-muted-foreground">
                    (optional JSON object, shallow-merged before resume)
                  </span>
                </Label>
                <Textarea
                  className="mt-1.5 font-mono text-[11px] min-h-[72px] resize-none"
                  value={patchText}
                  onChange={(e) => {
                    setPatchText(e.target.value);
                    setPatchError(null);
                  }}
                  placeholder='{"node_3": {"score": 0.95}}'
                  spellCheck={false}
                  disabled={disabled}
                />
                {patchError && (
                  <p className="text-xs text-destructive mt-1">{patchError}</p>
                )}
              </div>
            </div>
          </details>
        </div>

        <DialogFooter className="gap-2 sm:gap-2 pt-2 border-t">
          <Button
            variant="outline"
            onClick={() => handleAction("rejected")}
            disabled={disabled}
            className="text-destructive border-destructive/40 hover:bg-destructive/5 dark:hover:bg-destructive/10 gap-1.5"
          >
            <CircleX className="h-3.5 w-3.5" />
            {loading === "rejected" ? "Rejecting…" : "Reject"}
          </Button>
          <Button
            onClick={() => handleAction("approved")}
            disabled={disabled}
            className="gap-1.5"
          >
            <CircleCheck className="h-3.5 w-3.5" />
            {loading === "approved" ? "Approving…" : "Approve & resume"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
