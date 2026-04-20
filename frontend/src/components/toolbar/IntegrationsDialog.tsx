/**
 * IntegrationsDialog — CRUD UI for the tenant_integrations table.
 *
 * v1 scope: AutomationEdge only. The dialog hardcodes the system so
 * operators see a focused form per integration. When a second system
 * lands (Jenkins, Temporal, ...) we'll add a system picker.
 *
 * Each integration captures the connection defaults an AutomationEdge
 * node falls back to when its own config fields are blank —
 * ``baseUrl``, ``orgCode``, ``credentialsSecretPrefix``, etc. Secrets
 * themselves still live in the Secrets vault; this form only stores
 * the *prefix* that looks them up.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Bot,
  ChevronLeft,
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
import {
  api,
  type TenantIntegrationOut,
  type TenantIntegrationCreate,
  type TenantIntegrationUpdate,
} from "@/lib/api";
import {
  configToRecord,
  emptyAEConfig,
  recordToConfig,
  type AEConfig,
} from "./IntegrationsDialog.helpers";

const SYSTEM = "automationedge";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type View = "list" | "create" | "edit";

export function IntegrationsDialog({ open, onOpenChange }: Props) {
  const [view, setView] = useState<View>("list");
  const [rows, setRows] = useState<TenantIntegrationOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<TenantIntegrationOut | null>(null);

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
      refresh();
    });
    return () => {
      cancelled = true;
    };
  }, [open, refresh]);

  const handleDelete = (row: TenantIntegrationOut) => {
    if (
      !confirm(
        `Delete integration "${row.label}"? AutomationEdge nodes that reference it (or rely on it as the default) will stop resolving.`,
      )
    )
      return;
    api.deleteIntegration(row.id).then(refresh);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view !== "list" && (
              <button
                onClick={() => {
                  setView("list");
                  refresh();
                }}
                className="p-1 hover:bg-accent rounded"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            )}
            <Bot className="h-5 w-5" />
            {view === "list" && "AutomationEdge Integrations"}
            {view === "create" && "Add AutomationEdge Integration"}
            {view === "edit" && `Update ${editing?.label ?? "Integration"}`}
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {view === "list" && (
          <IntegrationListView
            rows={rows}
            loading={loading}
            onCreate={() => setView("create")}
            onEdit={(row) => {
              setEditing(row);
              setView("edit");
            }}
            onDelete={handleDelete}
          />
        )}

        {view === "create" && (
          <IntegrationCreateView
            onCreated={() => {
              setView("list");
              refresh();
            }}
          />
        )}

        {view === "edit" && editing && (
          <IntegrationEditView
            row={editing}
            onUpdated={() => {
              setView("list");
              refresh();
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/* --------------------------- list view --------------------------- */

function IntegrationListView({
  rows,
  loading,
  onCreate,
  onEdit,
  onDelete,
}: {
  rows: TenantIntegrationOut[];
  loading: boolean;
  onCreate: () => void;
  onEdit: (row: TenantIntegrationOut) => void;
  onDelete: (row: TenantIntegrationOut) => void;
}) {
  return (
    <>
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Connection defaults for AutomationEdge nodes. Nodes with a blank
          integration label use the default (starred) row.
        </p>
        <Button size="sm" onClick={onCreate} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Add Integration
        </Button>
      </div>
      <ScrollArea className="flex-1 min-h-0 max-h-[55vh]">
        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!loading && rows.length === 0 && (
          <div className="text-center py-8 space-y-2">
            <Bot className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              No AutomationEdge integrations yet.
            </p>
            <p className="text-xs text-muted-foreground">
              Add one to store the AE server URL + orgCode + credential prefix once,
              then reuse it from any AutomationEdge node.
            </p>
          </div>
        )}
        <div className="space-y-2">
          {rows.map((row) => {
            const cfg = recordToConfig(row.config_json);
            return (
              <div
                key={row.id}
                className="flex items-start gap-3 rounded-lg border px-4 py-3"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium font-mono truncate">
                      {row.label}
                    </p>
                    {row.is_default && (
                      <span
                        className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-200"
                        title="Default for blank integrationLabel"
                      >
                        <Star className="h-3 w-3 fill-amber-500 text-amber-500" />
                        default
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
                    {cfg.baseUrl || <span className="italic">(no base URL)</span>}
                  </p>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    orgCode <code>{cfg.orgCode || "?"}</code> ·{" "}
                    auth <code>{cfg.authMode}</code> ·{" "}
                    secret prefix <code>{cfg.credentialsSecretPrefix}</code>
                  </p>
                </div>
                <button
                  onClick={() => onEdit(row)}
                  className="p-1.5 text-muted-foreground hover:text-foreground transition-colors shrink-0"
                  title="Edit"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => onDelete(row)}
                  className="p-1.5 text-muted-foreground hover:text-red-500 transition-colors shrink-0"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </>
  );
}

/* --------------------------- create view ------------------------- */

function IntegrationCreateView({ onCreated }: { onCreated: () => void }) {
  const [label, setLabel] = useState("");
  const [config, setConfig] = useState<AEConfig>(emptyAEConfig());
  const [isDefault, setIsDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const canSubmit =
    label.trim() !== "" &&
    config.baseUrl.trim() !== "" &&
    config.orgCode.trim() !== "";

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setError("");
    try {
      const body: TenantIntegrationCreate = {
        system: SYSTEM,
        label: label.trim(),
        config_json: configToRecord(config),
        is_default: isDefault,
      };
      await api.createIntegration(body);
      onCreated();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to create integration";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <IntegrationForm
      label={label}
      onLabelChange={setLabel}
      config={config}
      onConfigChange={setConfig}
      isDefault={isDefault}
      onIsDefaultChange={setIsDefault}
      saving={saving}
      error={error}
      submitDisabled={!canSubmit}
      submitLabel="Save Integration"
      onSubmit={handleSubmit}
      labelEditable
    />
  );
}

/* --------------------------- edit view --------------------------- */

function IntegrationEditView({
  row,
  onUpdated,
}: {
  row: TenantIntegrationOut;
  onUpdated: () => void;
}) {
  const [label, setLabel] = useState(row.label);
  const [config, setConfig] = useState<AEConfig>(recordToConfig(row.config_json));
  const [isDefault, setIsDefault] = useState(row.is_default);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const canSubmit =
    label.trim() !== "" &&
    config.baseUrl.trim() !== "" &&
    config.orgCode.trim() !== "";

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setError("");
    try {
      const body: TenantIntegrationUpdate = {
        label: label.trim() !== row.label ? label.trim() : undefined,
        config_json: configToRecord(config),
        is_default: isDefault !== row.is_default ? isDefault : undefined,
      };
      await api.updateIntegration(row.id, body);
      onUpdated();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to update integration";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <IntegrationForm
      label={label}
      onLabelChange={setLabel}
      config={config}
      onConfigChange={setConfig}
      isDefault={isDefault}
      onIsDefaultChange={setIsDefault}
      saving={saving}
      error={error}
      submitDisabled={!canSubmit}
      submitLabel="Update Integration"
      onSubmit={handleSubmit}
      labelEditable
    />
  );
}

/* ----------------------- shared form body ------------------------ */

function IntegrationForm({
  label,
  onLabelChange,
  config,
  onConfigChange,
  isDefault,
  onIsDefaultChange,
  saving,
  error,
  submitDisabled,
  submitLabel,
  onSubmit,
  labelEditable,
}: {
  label: string;
  onLabelChange: (v: string) => void;
  config: AEConfig;
  onConfigChange: (c: AEConfig) => void;
  isDefault: boolean;
  onIsDefaultChange: (v: boolean) => void;
  saving: boolean;
  error: string;
  submitDisabled: boolean;
  submitLabel: string;
  onSubmit: () => void;
  labelEditable: boolean;
}) {
  return (
    <ScrollArea className="flex-1 min-h-0 max-h-[60vh] pr-2">
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Label</Label>
          <Input
            value={label}
            onChange={(e) => onLabelChange(e.target.value)}
            placeholder="prod-ae"
            className="font-mono"
            disabled={!labelEditable}
            autoFocus={labelEditable}
          />
          <p className="text-[10px] text-muted-foreground">
            Referenced by nodes as <code>integrationLabel</code>. Unique per tenant
            per system.
          </p>
        </div>

        <div className="space-y-2">
          <Label>AE REST base URL</Label>
          <Input
            value={config.baseUrl}
            onChange={(e) => onConfigChange({ ...config, baseUrl: e.target.value })}
            placeholder="http://ae.example.com:8080/aeengine/rest"
            className="font-mono"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label>AE orgCode</Label>
            <Input
              value={config.orgCode}
              onChange={(e) => onConfigChange({ ...config, orgCode: e.target.value })}
              placeholder="AEDEMO"
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label>Auth mode</Label>
            <select
              className="w-full h-8 rounded-md border border-border bg-background px-2 text-sm"
              value={config.authMode}
              onChange={(e) =>
                onConfigChange({
                  ...config,
                  authMode: e.target.value === "bearer" ? "bearer" : "ae_session",
                })
              }
            >
              <option value="ae_session">ae_session (username + password)</option>
              <option value="bearer">bearer (Authorization: Bearer)</option>
            </select>
          </div>
        </div>

        <div className="space-y-2">
          <Label>Credentials secret prefix</Label>
          <Input
            value={config.credentialsSecretPrefix}
            onChange={(e) =>
              onConfigChange({
                ...config,
                credentialsSecretPrefix: e.target.value,
              })
            }
            placeholder="AUTOMATIONEDGE"
            className="font-mono"
          />
          <p className="text-[10px] text-muted-foreground">
            ae_session reads <code>{"{" + "{ " + config.credentialsSecretPrefix + "_USERNAME }}"}</code>{" "}
            and <code>{"{" + "{ " + config.credentialsSecretPrefix + "_PASSWORD }}"}</code>;
            bearer reads <code>{"{" + "{ " + config.credentialsSecretPrefix + "_TOKEN }}"}</code>.
            Add these in the Secrets dialog first.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label>Source tag</Label>
            <Input
              value={config.source}
              onChange={(e) => onConfigChange({ ...config, source: e.target.value })}
              placeholder="AE AI Hub Orchestrator"
            />
          </div>
          <div className="space-y-2">
            <Label>AE userId</Label>
            <Input
              value={config.userId}
              onChange={(e) => onConfigChange({ ...config, userId: e.target.value })}
              placeholder="orchestrator"
              className="font-mono"
            />
          </div>
        </div>

        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => onIsDefaultChange(e.target.checked)}
            className="h-4 w-4"
          />
          <span className="text-sm">
            Use as default for this tenant
          </span>
        </label>

        {error && <p className="text-sm text-red-500">{error}</p>}

        <div className="flex justify-end pt-2">
          <Button
            onClick={onSubmit}
            disabled={submitDisabled || saving}
            className="gap-1.5"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitLabel}
          </Button>
        </div>
      </div>
    </ScrollArea>
  );
}
