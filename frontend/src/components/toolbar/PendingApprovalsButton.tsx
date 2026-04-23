/**
 * PendingApprovalsButton (HITL-01.b)
 *
 * Toolbar affordance for the pending-approvals dashboard. Makes
 * approvals discoverable instead of hiding them inside per-instance
 * ExecutionPanel views.
 *
 * Visual language (matches the rest of the toolbar):
 *   - Bell icon (universal "notifications" affordance).
 *   - Amber dot pulsing over the bell when count > 0 — matches the
 *     amber we already use for suspended-state everywhere else.
 *   - Count badge at bottom-right of the bell, caps at "9+" so the
 *     button never grows unpredictably wide.
 *   - Click → dropdown with grouped rows (one per suspended
 *     instance), oldest-first.
 *   - Click a row → HITLResumeDialog pre-filled (decision flow
 *     stays on the existing dialog; this component is pure
 *     discovery).
 *   - Polls every 30s while mounted; refreshes on dialog close so
 *     the count is accurate the moment an operator approves.
 *   - Empty state: centred sentence "No approvals waiting — nice."
 *     (not a sterile "No data" icon).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bell,
  CheckCircle2,
  Clock,
  RefreshCw,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { api, type PendingApproval } from "@/lib/api";
import { useWorkflowStore } from "@/store/workflowStore";
import { HITLResumeDialog } from "@/components/toolbar/HITLResumeDialog";


const POLL_INTERVAL_MS = 30_000;


export function PendingApprovalsButton() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<PendingApproval[]>([]);
  const [selected, setSelected] = useState<PendingApproval | null>(null);

  const fetchInstanceContext = useWorkflowStore((s) => s.fetchInstanceContext);
  const instanceContext = useWorkflowStore((s) => s.instanceContext);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listPendingApprovals();
      setRows(list);
    } catch {
      // Silent fail — the badge just won't update. Don't toast
      // every poll; operators don't want background noise.
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll while mounted. Using an interval rather than WebSocket
  // because suspend events are rare (human-cadence) and a 30s
  // lag on the badge is imperceptible for this UX.
  useEffect(() => {
    void refresh();
    const id = setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // When the dialog closes, refresh immediately so the count
  // drops the moment the approver's decision lands.
  const handleDialogClose = useCallback(() => {
    setSelected(null);
    void refresh();
  }, [refresh]);

  // Click-through: fetch the full context for this instance (so
  // HITLResumeDialog renders with the patch editor ready) then
  // open the dialog.
  const handleRowClick = useCallback(
    async (row: PendingApproval) => {
      setOpen(false);
      await fetchInstanceContext(row.workflow_id, row.instance_id);
      setSelected(row);
    },
    [fetchInstanceContext],
  );

  const groupedByWorkflow = useMemo(() => {
    const groups = new Map<string, { name: string; rows: PendingApproval[] }>();
    for (const row of rows) {
      const existing = groups.get(row.workflow_id);
      if (existing) {
        existing.rows.push(row);
      } else {
        groups.set(row.workflow_id, { name: row.workflow_name, rows: [row] });
      }
    }
    return Array.from(groups.entries());
  }, [rows]);

  const count = rows.length;
  const countLabel = count > 9 ? "9+" : String(count);

  return (
    <>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="relative gap-1.5"
            aria-label={
              count === 0
                ? "No pending approvals"
                : `${count} pending ${count === 1 ? "approval" : "approvals"}`
            }
            title={
              count === 0
                ? "No approvals waiting"
                : `${count} ${count === 1 ? "approval" : "approvals"} waiting`
            }
          >
            <Bell className="h-4 w-4" />
            {count > 0 && (
              <>
                {/* Pulsing amber dot — matches the suspended-state
                    amber we use everywhere else in the UI. */}
                <span className="absolute top-1 right-1 flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
                </span>
                <Badge
                  variant="outline"
                  className="h-5 px-1.5 text-[10px] font-medium border-amber-300 text-amber-700 dark:text-amber-300 dark:border-amber-800"
                >
                  {countLabel}
                </Badge>
              </>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-96 p-0">
          <div className="flex items-center justify-between px-3 py-2 border-b">
            <div className="flex items-center gap-1.5">
              <Bell className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-sm font-medium">Pending approvals</span>
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1 disabled:opacity-50"
              aria-label="Refresh pending approvals"
              title="Refresh"
            >
              <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
            </button>
          </div>

          {count === 0 ? (
            <EmptyState />
          ) : (
            <ScrollArea className="max-h-[420px]">
              <ul className="py-1">
                {groupedByWorkflow.map(([workflowId, group]) => (
                  <li key={workflowId} className="py-1">
                    <div className="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
                      {group.name}
                    </div>
                    {group.rows.map((row) => (
                      <ApprovalRow
                        key={row.instance_id}
                        row={row}
                        onClick={() => void handleRowClick(row)}
                      />
                    ))}
                  </li>
                ))}
              </ul>
            </ScrollArea>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {selected && instanceContext && (
        <HITLResumeDialog
          open={true}
          onClose={handleDialogClose}
          workflowId={selected.workflow_id}
          instanceId={selected.instance_id}
          context={instanceContext}
        />
      )}
    </>
  );
}


function EmptyState() {
  return (
    <div className="px-4 py-8 text-center">
      <div className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-emerald-50 dark:bg-emerald-950/30 mb-2.5">
        <CheckCircle2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
      </div>
      <p className="text-sm font-medium">No approvals waiting — nice.</p>
      <p className="text-[11px] text-muted-foreground mt-1 max-w-[18rem] mx-auto leading-relaxed">
        When a workflow pauses at a Human Approval node, it'll appear here
        so you can decide without hunting through the execution panel.
      </p>
    </div>
  );
}


function ApprovalRow({
  row,
  onClick,
}: {
  row: PendingApproval;
  onClick: () => void;
}) {
  const absoluteTime = new Date(row.suspended_at).toLocaleString();
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left px-3 py-2 hover:bg-accent/60 transition-colors block"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {row.approval_message ? (
            <p className="text-sm leading-snug line-clamp-2">
              {row.approval_message}
            </p>
          ) : (
            <p className="text-sm italic text-muted-foreground">
              (no approval message set)
            </p>
          )}
          <p className="mt-0.5 text-[10px] text-muted-foreground font-mono">
            node {row.node_id}
          </p>
        </div>
        <div
          className="shrink-0 flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-300"
          title={absoluteTime}
        >
          <Clock className="h-3 w-3" />
          {formatAge(row.age_seconds)}
        </div>
      </div>
    </button>
  );
}


/**
 * "3m ago" / "2h ago" / "3d ago" — relative timestamp. The
 * absolute timestamp is surfaced in the row's tooltip so an
 * operator hunting for precision doesn't have to do mental math.
 */
export function formatAge(seconds: number): string {
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
