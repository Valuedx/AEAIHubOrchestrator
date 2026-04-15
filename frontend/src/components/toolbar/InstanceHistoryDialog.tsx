import { useEffect } from "react";
import { Activity, ExternalLink, Loader2, CircleDot, CircleCheck, CircleX, Pause, PauseCircle, Ban } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useWorkflowStore } from "@/store/workflowStore";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const STATUS_ICONS: Record<string, { icon: any; color: string }> = {
  queued: { icon: CircleDot, color: "text-muted-foreground" },
  running: { icon: Loader2, color: "text-blue-500 animate-spin" },
  completed: { icon: CircleCheck, color: "text-green-500" },
  failed: { icon: CircleX, color: "text-red-500" },
  suspended: { icon: Pause, color: "text-yellow-500" },
  paused: { icon: PauseCircle, color: "text-cyan-600" },
  cancelled: { icon: Ban, color: "text-orange-500" },
};

export function InstanceHistoryDialog({ open, onOpenChange }: Props) {
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const instances = useWorkflowStore((s) => s.instances);
  const loading = useWorkflowStore((s) => s.loading);
  const fetchInstances = useWorkflowStore((s) => s.fetchInstances);
  const openInstanceFromHistory = useWorkflowStore((s) => s.openInstanceFromHistory);

  useEffect(() => {
    if (open && currentWorkflow) {
      fetchInstances(currentWorkflow.id);
    }
  }, [open, currentWorkflow, fetchInstances]);

  const handleOpenInstance = async (instanceId: string) => {
    if (!currentWorkflow) return;
    await openInstanceFromHistory(currentWorkflow.id, instanceId);
    if (!useWorkflowStore.getState().error) {
      onOpenChange(false);
    }
  };

  if (!currentWorkflow) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Execution History — {currentWorkflow.name}
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : instances.length === 0 ? (
          <div className="py-12 text-center text-sm text-muted-foreground">
            No execution history found for this workflow.
          </div>
        ) : (
          <ScrollArea className="max-h-[450px]">
            <div className="space-y-1 p-1">
              {instances.map((inst) => {
                const StatusIcon = STATUS_ICONS[inst.status]?.icon || CircleDot;
                const statusColor = STATUS_ICONS[inst.status]?.color || "";
                
                return (
                  <div
                    key={inst.id}
                    className="flex items-center gap-4 rounded-md px-3 py-3 hover:bg-accent transition-colors border border-transparent hover:border-border"
                  >
                    <div className={`shrink-0 ${statusColor}`}>
                      <StatusIcon className="h-4 w-4" />
                    </div>
                    
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-muted-foreground truncate">
                          {inst.id.slice(0, 8)}...
                        </span>
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0 capitalize">
                          {inst.status}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-2 mt-1 text-[11px] text-muted-foreground">
                        <span>Started: {new Date(inst.created_at).toLocaleString()}</span>
                        {inst.completed_at && (
                          <>
                            <span>•</span>
                            <span>Finished: {new Date(inst.completed_at).toLocaleTimeString()}</span>
                          </>
                        )}
                      </div>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 gap-1.5 text-xs shrink-0"
                      onClick={() => handleOpenInstance(inst.id)}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      View Logs
                    </Button>
                  </div>
                );
              })}
            </div>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}
