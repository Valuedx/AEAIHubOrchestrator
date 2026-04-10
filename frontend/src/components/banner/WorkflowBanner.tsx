import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWorkflowStore } from "@/store/workflowStore";

export function WorkflowBanner() {
  const error = useWorkflowStore((s) => s.error);
  const notice = useWorkflowStore((s) => s.notice);
  const dismissError = useWorkflowStore((s) => s.dismissError);
  const dismissNotice = useWorkflowStore((s) => s.dismissNotice);

  if (error) {
    return (
      <div
        role="alert"
        className="shrink-0 flex items-start gap-2 border-b border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
      >
        <p className="flex-1 min-w-0 break-words">{error}</p>
        <Button type="button" variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" onClick={dismissError}>
          <X className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  if (notice) {
    return (
      <div
        role="status"
        className="shrink-0 flex items-start gap-2 border-b border-sky-500/40 bg-sky-50 dark:bg-sky-950/40 px-3 py-2 text-sm text-sky-900 dark:text-sky-100"
      >
        <p className="flex-1 min-w-0 break-words">{notice}</p>
        <Button type="button" variant="ghost" size="sm" className="h-7 w-7 shrink-0 p-0" onClick={dismissNotice}>
          <X className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  return null;
}
