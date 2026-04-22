import { useEffect } from "react";
import { Trash2, ExternalLink, Loader2, Copy, PowerOff } from "lucide-react";
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

export function WorkflowListDialog({ open, onOpenChange }: Props) {
  const workflows = useWorkflowStore((s) => s.workflows);
  const loading = useWorkflowStore((s) => s.loading);
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const fetchWorkflows = useWorkflowStore((s) => s.fetchWorkflows);
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow);
  const deleteWorkflow = useWorkflowStore((s) => s.deleteWorkflow);
  const duplicateWorkflow = useWorkflowStore((s) => s.duplicateWorkflow);

  useEffect(() => {
    if (open) fetchWorkflows();
  }, [open, fetchWorkflows]);

  const handleLoad = (id: string) => {
    loadWorkflow(id);
    onOpenChange(false);
  };

  const handleDelete = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm("Delete this workflow? This cannot be undone.")) {
      deleteWorkflow(id);
    }
  };

  const handleDuplicate = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    duplicateWorkflow(id);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>Saved Workflows</DialogTitle>
        </DialogHeader>
        <Separator />
        <div className="flex-1 min-h-0 flex flex-col">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : workflows.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              No saved workflows yet. Build a graph and click Save.
            </div>
          ) : (
            <ScrollArea className="flex-1 min-h-0 max-h-[60vh] pr-4">
              <div className="space-y-1 p-1">
                {workflows.map((wf) => (
                  <button
                    key={wf.id}
                    onClick={() => handleLoad(wf.id)}
                    className={`w-full flex items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors hover:bg-accent ${
                      currentWorkflow?.id === wf.id ? "bg-accent" : ""
                    } ${wf.is_active === false ? "opacity-60" : ""}`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium truncate">{wf.name}</span>
                        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
                          v{wf.version}
                        </Badge>
                        {wf.is_active === false && (
                          <Badge
                            variant="outline"
                            className="text-[10px] px-1.5 py-0 shrink-0 text-muted-foreground gap-1"
                            title="Schedule Triggers paused; manual Run still works"
                          >
                            <PowerOff className="h-2.5 w-2.5" />
                            inactive
                          </Badge>
                        )}
                      </div>
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        Updated {new Date(wf.updated_at).toLocaleDateString()} {new Date(wf.updated_at).toLocaleTimeString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                        onClick={(e) => handleDuplicate(wf.id, e)}
                        title="Duplicate workflow"
                      >
                        <Copy className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                        onClick={(e) => handleDelete(wf.id, e)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
