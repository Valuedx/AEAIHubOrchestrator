import { useEffect, useState } from "react";
import { History, RotateCcw, Loader2 } from "lucide-react";
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
import { api } from "@/lib/api";
import type { SnapshotOut } from "@/lib/api";
import { useWorkflowStore } from "@/store/workflowStore";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function VersionHistoryDialog({ open, onOpenChange }: Props) {
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow);

  const [snapshots, setSnapshots] = useState<SnapshotOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [rolling, setRolling] = useState<number | null>(null);

  useEffect(() => {
    if (!open || !currentWorkflow) return;
    setLoading(true);
    api.listVersions(currentWorkflow.id)
      .then(setSnapshots)
      .finally(() => setLoading(false));
  }, [open, currentWorkflow]);

  const handleRollback = async (version: number) => {
    if (!currentWorkflow) return;
    if (!confirm(`Restore to v${version}? A snapshot of the current state will be saved first.`)) return;
    setRolling(version);
    try {
      await api.rollbackVersion(currentWorkflow.id, version);
      await loadWorkflow(currentWorkflow.id);
      onOpenChange(false);
    } catch (err) {
      alert(`Rollback failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRolling(null);
    }
  };

  if (!currentWorkflow) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Version History</DialogTitle>
          </DialogHeader>
          <div className="py-8 text-center text-sm text-muted-foreground">
            Save a workflow first to view version history.
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="h-4 w-4" />
            Version History — {currentWorkflow.name}
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {/* Current version */}
        <div className="flex items-center gap-3 px-3 py-2.5 rounded-md bg-accent border border-border">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <Badge variant="default" className="text-[10px] px-1.5 py-0">
                v{currentWorkflow.version}
              </Badge>
              <span className="text-xs font-medium text-foreground">Current</span>
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Saved {new Date(currentWorkflow.updated_at).toLocaleString()}
            </p>
          </div>
        </div>

        <Separator />

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : snapshots.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted-foreground">
            No saved snapshots yet. Edit and save this workflow to create history.
          </div>
        ) : (
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-1 p-1">
              {snapshots.map((snap) => (
                <div
                  key={snap.id}
                  className="flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-accent transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
                        v{snap.version}
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      {snap.saved_at
                        ? new Date(snap.saved_at).toLocaleString()
                        : "Unknown date"}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1.5 text-xs shrink-0"
                    disabled={rolling === snap.version}
                    onClick={() => handleRollback(snap.version)}
                    title={`Restore to v${snap.version}`}
                  >
                    {rolling === snap.version ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3.5 w-3.5" />
                    )}
                    Restore
                  </Button>
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}
