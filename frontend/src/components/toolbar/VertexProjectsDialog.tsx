/**
 * VERTEX-02 — per-tenant Vertex AI project registry.
 *
 * Rows live in the same ``tenant_integrations`` table as AutomationEdge,
 * keyed by ``system="vertex"``. ``config_json`` shape is small:
 *
 *   { "project": "<gcp-project-id>", "location": "<region>" }
 *
 * At most one row per tenant is ``is_default=true`` — the LLM node
 * dispatch picks that row when a tenant uses ``provider: "vertex"``.
 * If no row exists, ``ORCHESTRATOR_VERTEX_PROJECT`` +
 * ``ORCHESTRATOR_VERTEX_LOCATION`` env vars are the fallback (see
 * ``engine/llm_providers._resolve_vertex_target``).
 *
 * Raw credentials are NOT stored here — ADC is still process-global,
 * so the service-account identity running the orchestrator needs
 * ``aiplatform.user`` on every target project. That caveat is surfaced
 * inline so operators don't set up a row expecting per-tenant auth.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft,
  Cloud,
  Loader2,
  Pencil,
  Plus,
  Star,
  Trash2,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { api, type TenantIntegrationOut } from "@/lib/api";

const SYSTEM = "vertex";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type View = "list" | "form";

interface FormState {
  label: string;
  project: string;
  location: string;
  is_default: boolean;
}

const EMPTY_FORM: FormState = {
  label: "",
  project: "",
  location: "us-central1",
  is_default: false,
};


export function VertexProjectsDialog({ open, onOpenChange }: Props) {
  const [view, setView] = useState<View>("list");
  const [rows, setRows] = useState<TenantIntegrationOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<TenantIntegrationOut | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    api
      .listIntegrations(SYSTEM)
      .then(setRows)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setView("list");
      setEditing(null);
      setForm(EMPTY_FORM);
      setSaveErr(null);
      refresh();
    });
    return () => {
      cancelled = true;
    };
  }, [open, refresh]);

  const startCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setSaveErr(null);
    setView("form");
  };

  const startEdit = (row: TenantIntegrationOut) => {
    setEditing(row);
    const cfg = row.config_json as { project?: string; location?: string };
    setForm({
      label: row.label,
      project: cfg?.project ?? "",
      location: cfg?.location ?? "us-central1",
      is_default: row.is_default,
    });
    setSaveErr(null);
    setView("form");
  };

  const handleDelete = async (row: TenantIntegrationOut) => {
    if (
      !confirm(
        `Delete Vertex project "${row.label}"? Nodes using provider="vertex" will fall back to ORCHESTRATOR_VERTEX_PROJECT (if set) or fail at dispatch.`,
      )
    )
      return;
    await api.deleteIntegration(row.id);
    refresh();
  };

  const handleSave = async () => {
    setSaveErr(null);
    const label = form.label.trim();
    const project = form.project.trim();
    const location = form.location.trim() || "us-central1";
    if (!label || !project) {
      setSaveErr("Label and GCP project ID are required.");
      return;
    }

    const config = { project, location };
    try {
      if (editing) {
        await api.updateIntegration(editing.id, {
          label,
          config_json: config,
          is_default: form.is_default,
        });
      } else {
        await api.createIntegration({
          system: SYSTEM,
          label,
          config_json: config,
          is_default: form.is_default,
        });
      }
      setView("list");
      refresh();
    } catch (e) {
      setSaveErr(String(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view === "form" && (
              <button
                type="button"
                onClick={() => setView("list")}
                className="p-0.5 rounded hover:bg-accent"
                aria-label="Back"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            )}
            <Cloud className="h-4 w-4" />
            {view === "list"
              ? "Vertex AI Projects"
              : editing
                ? `Edit ${editing.label}`
                : "Add Vertex Project"}
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {view === "list" && (
          <>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : rows.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground px-4 space-y-2">
                <p>No Vertex AI projects registered for this tenant.</p>
                <p className="text-[11px]">
                  Nodes using <code className="text-[10px] bg-muted px-1 py-0.5 rounded">provider: "vertex"</code>
                  will fall back to
                  <code className="mx-1 text-[10px] bg-muted px-1 py-0.5 rounded">ORCHESTRATOR_VERTEX_PROJECT</code>
                  until you add one.
                </p>
              </div>
            ) : (
              <ScrollArea className="flex-1 min-h-0">
                <div className="space-y-1 p-1">
                  {rows.map((row) => {
                    const cfg = row.config_json as { project?: string; location?: string };
                    return (
                      <div
                        key={row.id}
                        className="flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-accent"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium truncate">{row.label}</span>
                            {row.is_default && (
                              <Star
                                className="h-3 w-3 text-yellow-500 shrink-0"
                                fill="currentColor"
                                aria-label="Default"
                              />
                            )}
                          </div>
                          <p className="text-[11px] text-muted-foreground truncate font-mono">
                            {cfg?.project ?? "(no project)"} · {cfg?.location ?? "us-central1"}
                          </p>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0"
                            onClick={() => startEdit(row)}
                            title="Edit"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                            onClick={() => handleDelete(row)}
                            title="Delete"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            )}
            <div className="space-y-2 pt-2 px-1">
              <p className="text-[11px] text-muted-foreground">
                Auth is still process-global (Application Default Credentials). The
                orchestrator's service account needs
                <code className="mx-1 text-[10px] bg-muted px-1 py-0.5 rounded">aiplatform.user</code>
                on every project listed here.
              </p>
              <div className="flex justify-end">
                <Button variant="default" size="sm" onClick={startCreate} className="gap-1.5">
                  <Plus className="h-4 w-4" /> Add project
                </Button>
              </div>
            </div>
          </>
        )}

        {view === "form" && (
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-3 p-1">
              <div className="space-y-1.5">
                <Label htmlFor="vertex-label">Label</Label>
                <Input
                  id="vertex-label"
                  value={form.label}
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                  placeholder="prod-vertex"
                />
                <p className="text-[11px] text-muted-foreground">
                  Human-readable name. Used only in this dialog — nodes pick the default row.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="vertex-project">GCP project ID</Label>
                <Input
                  id="vertex-project"
                  value={form.project}
                  onChange={(e) => setForm({ ...form, project: e.target.value })}
                  placeholder="my-tenant-gcp-project"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="vertex-location">Region</Label>
                <Input
                  id="vertex-location"
                  value={form.location}
                  onChange={(e) => setForm({ ...form, location: e.target.value })}
                  placeholder="us-central1"
                />
                <p className="text-[11px] text-muted-foreground">
                  For data-residency requirements pick a specific region (e.g. <code>europe-west4</code>).
                </p>
              </div>

              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={(e) =>
                    setForm({ ...form, is_default: e.target.checked })
                  }
                  className="rounded border-input"
                />
                Use as default for this tenant's Vertex calls
              </label>

              {saveErr && (
                <p className="text-xs text-destructive">{saveErr}</p>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="ghost" size="sm" onClick={() => setView("list")}>
                  Cancel
                </Button>
                <Button variant="default" size="sm" onClick={handleSave}>
                  {editing ? "Save" : "Create"}
                </Button>
              </div>
            </div>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}
