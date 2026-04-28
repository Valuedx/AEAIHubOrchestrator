import { useMemo, useRef, useState } from "react";
import {
  LayoutTemplate,
  Search,
  Upload,
  Download,
  Loader2,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  WORKFLOW_TEMPLATES,
  TEMPLATE_CATEGORIES,
  type TemplateCategory,
} from "@/lib/templates";
import { useWorkflowStore } from "@/store/workflowStore";
import { useFlowStore } from "@/store/flowStore";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function TemplateGalleryDialog({ open, onOpenChange }: Props) {
  const [category, setCategory] = useState<TemplateCategory | "all">("all");
  const [query, setQuery] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const loading = useWorkflowStore((s) => s.loading);
  const loadTemplate = useWorkflowStore((s) => s.loadTemplate);
  const exportCurrentGraph = useWorkflowStore((s) => s.exportCurrentGraph);
  const importGraphJson = useWorkflowStore((s) => s.importGraphJson);
  const nodes = useFlowStore((s) => s.nodes);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return WORKFLOW_TEMPLATES.filter((t) => {
      if (category !== "all" && t.category !== category) return false;
      if (!q) return true;
      const hay = `${t.name} ${t.description} ${t.tags.join(" ")}`.toLowerCase();
      return hay.includes(q);
    });
  }, [category, query]);

  const handleUseTemplate = (id: string) => {
    const dirty = useWorkflowStore.getState().isDirty;
    const hasNodes = nodes.length > 0;
    if (
      (dirty || hasNodes) &&
      !window.confirm(
        "Replace the current canvas with this template? Unsaved changes will be lost unless you saved.",
      )
    ) {
      return;
    }
    loadTemplate(id);
    onOpenChange(false);
  };

  const handleExport = () => {
    const name =
      useWorkflowStore.getState().currentWorkflow?.name?.replace(/\s+/g, "-") ||
      "workflow-graph";
    const blob = exportCurrentGraph();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const MAX_IMPORT_BYTES = 5 * 1024 * 1024; // 5 MB
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > MAX_IMPORT_BYTES) {
      window.alert(`File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max import size is 5 MB.`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const raw = reader.result as string;
        const data = JSON.parse(raw) as unknown;
        if (
          !data ||
          typeof data !== "object" ||
          !Array.isArray((data as { nodes?: unknown }).nodes) ||
          !Array.isArray((data as { edges?: unknown }).edges)
        ) {
          window.alert("Invalid file: expected JSON with { nodes: [], edges: [] }.");
          return;
        }
        const importedNodes = (data as { nodes: unknown[] }).nodes;
        const importedEdges = (data as { edges: unknown[] }).edges;

        const invalidNodes = importedNodes.filter((n) => {
          if (!n || typeof n !== "object") return true;
          const node = n as Record<string, unknown>;
          return !node.id || !node.type || !node.position || !node.data;
        });
        if (invalidNodes.length > 0) {
          window.alert(
            `${invalidNodes.length} node(s) are missing required fields (id, type, position, data). ` +
            "Please fix the JSON and try again.",
          );
          return;
        }

        const invalidEdges = importedEdges.filter((e) => {
          if (!e || typeof e !== "object") return true;
          const edge = e as Record<string, unknown>;
          return !edge.source || !edge.target;
        });
        if (invalidEdges.length > 0) {
          window.alert(
            `${invalidEdges.length} edge(s) are missing required fields (source, target). ` +
            "Please fix the JSON and try again.",
          );
          return;
        }

        const dirty = useWorkflowStore.getState().isDirty;
        const hasNodes = nodes.length > 0;
        if (
          (dirty || hasNodes) &&
          !window.confirm(
            "Replace the current canvas with the imported graph? Unsaved changes may be lost.",
          )
        ) {
          return;
        }
        importGraphJson({ nodes: importedNodes, edges: importedEdges });
        onOpenChange(false);
      } catch {
        window.alert("Could not parse JSON file.");
      }
    };
    reader.readAsText(file);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LayoutTemplate className="h-5 w-5" />
            Template gallery
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Starter graphs you can customize and save. Same portable{" "}
          <code className="text-xs bg-muted px-1 rounded">graph_json</code> format as the API.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant={category === "all" ? "secondary" : "outline"}
            size="sm"
            className="h-8 text-xs"
            onClick={() => setCategory("all")}
          >
            All
          </Button>
          {TEMPLATE_CATEGORIES.map((c) => (
            <Button
              key={c.id}
              variant={category === c.id ? "secondary" : "outline"}
              size="sm"
              className="h-8 text-xs"
              onClick={() => setCategory(c.id)}
            >
              {c.label}
            </Button>
          ))}
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search templates…"
            className="pl-9 h-9"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Separator />
        <ScrollArea className="flex-1 min-h-0 pr-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {filtered.map((t) => (
              <div
                key={t.id}
                className="rounded-lg border bg-card p-3 flex flex-col gap-2 shadow-sm"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold leading-tight">{t.name}</h3>
                    <p className="text-[11px] text-muted-foreground mt-1 line-clamp-3">
                      {t.description}
                    </p>
                  </div>
                  <Badge variant="outline" className="text-[10px] shrink-0">
                    {t.nodeCount} nodes
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-1">
                  {t.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="text-[9px] px-1.5 py-0">
                      {tag}
                    </Badge>
                  ))}
                </div>
                <Button
                  size="sm"
                  className="mt-auto w-full"
                  disabled={loading}
                  onClick={() => handleUseTemplate(t.id)}
                >
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Use template"}
                </Button>
              </div>
            ))}
          </div>
          {filtered.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-8">No templates match.</p>
          )}
        </ScrollArea>
        <Separator />
        <div className="flex flex-wrap gap-2 justify-between items-center">
          <span className="text-xs text-muted-foreground">Import / export portable graph JSON</span>
          <div className="flex gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={handleImportFile}
            />
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="h-3.5 w-3.5" />
              Import JSON
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={handleExport}
              disabled={nodes.length === 0}
            >
              <Download className="h-3.5 w-3.5" />
              Export current
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
