/**
 * ADMIN-01 — per-tenant policy overrides dialog.
 *
 * One row per tenant (singleton). Each field can either take an
 * override value or fall through to the env default. The UI shows
 * the *effective* value + a source pill ("override" / "env default")
 * so operators can see at a glance which knobs are inherited.
 *
 * Three pending actions per field:
 *   * Unchanged          — omit from PATCH body
 *   * Set to a number    — send the number
 *   * Reset to env default — send explicit null (clears the override)
 *
 * The PATCH handler distinguishes between omitted and null via
 * ``model_fields_set`` on the backend so the three states work.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Loader2,
  RotateCcw,
  SlidersHorizontal,
  X,
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
import { Badge } from "@/components/ui/badge";
import {
  api,
  type TenantPolicyOut,
  type TenantPolicyUpdate,
  type TenantPolicySource,
} from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type FieldKey = "execution_quota_per_hour" | "max_snapshots" | "mcp_pool_size";

interface FieldMeta {
  key: FieldKey;
  label: string;
  unit: string;
  description: string;
  minEffective: number;
}

const FIELDS: FieldMeta[] = [
  {
    key: "execution_quota_per_hour",
    label: "Execution quota (per hour)",
    unit: "runs / hour",
    description:
      "Max workflow executions per hour for this tenant. POST /execute returns 429 beyond this. Env default: ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR.",
    minEffective: 1,
  },
  {
    key: "max_snapshots",
    label: "Max version snapshots",
    unit: "snapshots / workflow",
    description:
      "Retention cap per workflow for graph version history. 0 = unlimited. Enforced by the daily Beat prune task. Env default: ORCHESTRATOR_MAX_SNAPSHOTS.",
    minEffective: 0,
  },
  {
    key: "mcp_pool_size",
    label: "MCP pool size",
    unit: "warm sessions per (tenant, server)",
    description:
      "Warm MCP client sessions this tenant can hold per server. Changes apply when pools are (re)constructed — typically next app restart. Env default: ORCHESTRATOR_MCP_POOL_SIZE.",
    minEffective: 1,
  },
];


type Pending = { mode: "unchanged" } | { mode: "set"; value: number } | { mode: "reset" };

function emptyPending(): Record<FieldKey, Pending> {
  return {
    execution_quota_per_hour: { mode: "unchanged" },
    max_snapshots: { mode: "unchanged" },
    mcp_pool_size: { mode: "unchanged" },
  };
}


export function TenantPolicyDialog({ open, onOpenChange }: Props) {
  const [policy, setPolicy] = useState<TenantPolicyOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pending, setPending] = useState<Record<FieldKey, Pending>>(emptyPending());
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    api
      .getTenantPolicy()
      .then((p) => {
        setPolicy(p);
        setPending(emptyPending());
        setErr(null);
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      refresh();
    });
    return () => {
      cancelled = true;
    };
  }, [open, refresh]);

  const hasChanges = useMemo(
    () => Object.values(pending).some((p) => p.mode !== "unchanged"),
    [pending],
  );

  const handleInputChange = (key: FieldKey, raw: string, meta: FieldMeta) => {
    if (raw === "") {
      setPending({ ...pending, [key]: { mode: "unchanged" } });
      return;
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || !Number.isInteger(n) || n < meta.minEffective) {
      // Defer the strict validation to Save — let the user type freely;
      // we'll surface a friendly error on submit.
      setPending({ ...pending, [key]: { mode: "set", value: n } });
      return;
    }
    setPending({ ...pending, [key]: { mode: "set", value: n } });
  };

  const handleReset = (key: FieldKey) => {
    setPending({ ...pending, [key]: { mode: "reset" } });
  };

  const handleCancelPending = (key: FieldKey) => {
    setPending({ ...pending, [key]: { mode: "unchanged" } });
  };

  const handleSave = async () => {
    setErr(null);

    // Validate: any "set" must be a positive integer (or 0 for max_snapshots).
    for (const meta of FIELDS) {
      const p = pending[meta.key];
      if (p.mode === "set") {
        if (!Number.isInteger(p.value) || p.value < meta.minEffective) {
          setErr(`${meta.label} must be an integer ≥ ${meta.minEffective}.`);
          return;
        }
      }
    }

    const body: TenantPolicyUpdate = {};
    for (const meta of FIELDS) {
      const p = pending[meta.key];
      if (p.mode === "set") body[meta.key] = p.value;
      else if (p.mode === "reset") body[meta.key] = null;
    }

    setSaving(true);
    try {
      const updated = await api.updateTenantPolicy(body);
      setPolicy(updated);
      setPending(emptyPending());
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4" />
            Tenant Policy
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {loading || policy === null ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-5 p-1">
              <p className="text-[11px] text-muted-foreground">
                These knobs used to live only as <code className="text-[10px] bg-muted px-1 py-0.5 rounded">ORCHESTRATOR_*</code> env vars. Each can now be overridden per tenant; a cleared override falls back to the env default.
              </p>

              {FIELDS.map((meta) => (
                <PolicyFieldRow
                  key={meta.key}
                  meta={meta}
                  effectiveValue={policy.values[meta.key]}
                  source={policy.source[meta.key]}
                  pending={pending[meta.key]}
                  onInputChange={(raw) => handleInputChange(meta.key, raw, meta)}
                  onReset={() => handleReset(meta.key)}
                  onCancelPending={() => handleCancelPending(meta.key)}
                />
              ))}

              {err && <p className="text-xs text-destructive">{err}</p>}

              <div className="flex justify-end gap-2 pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setPending(emptyPending())}
                  disabled={!hasChanges || saving}
                >
                  Discard
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  onClick={handleSave}
                  disabled={!hasChanges || saving}
                >
                  {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
                </Button>
              </div>
            </div>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}


// ---------------------------------------------------------------------------
// Single-field row
// ---------------------------------------------------------------------------


interface RowProps {
  meta: FieldMeta;
  effectiveValue: number;
  source: TenantPolicySource;
  pending: Pending;
  onInputChange: (raw: string) => void;
  onReset: () => void;
  onCancelPending: () => void;
}

function PolicyFieldRow({
  meta,
  effectiveValue,
  source,
  pending,
  onInputChange,
  onReset,
  onCancelPending,
}: RowProps) {
  // What the input should show right now: the pending value if one
  // exists, otherwise the current override value (or blank if the
  // field is currently at env default).
  const inputValue =
    pending.mode === "set"
      ? String(pending.value)
      : pending.mode === "reset"
        ? ""
        : source === "tenant_policy"
          ? String(effectiveValue)
          : "";

  const placeholder =
    source === "env_default" && pending.mode !== "set"
      ? `${effectiveValue} (env default)`
      : undefined;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="font-medium">{meta.label}</Label>
        <SourcePill source={source} pending={pending} />
      </div>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          inputMode="numeric"
          min={meta.minEffective}
          value={inputValue}
          placeholder={placeholder}
          onChange={(e) => onInputChange(e.target.value)}
          className="max-w-[200px]"
        />
        <span className="text-[11px] text-muted-foreground">{meta.unit}</span>
        <div className="flex-1" />
        {pending.mode !== "unchanged" && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-[11px]"
            onClick={onCancelPending}
            title="Cancel this pending change"
          >
            <X className="h-3 w-3" /> undo
          </Button>
        )}
        {source === "tenant_policy" && pending.mode !== "reset" && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onReset}
            title="Clear this tenant override — fall back to the env default"
          >
            <RotateCcw className="h-3 w-3" /> reset
          </Button>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">{meta.description}</p>
    </div>
  );
}


function SourcePill({
  source,
  pending,
}: {
  source: TenantPolicySource;
  pending: Pending;
}) {
  if (pending.mode === "set") {
    return (
      <Badge variant="outline" className="text-[10px] bg-yellow-500/10 border-yellow-500/30 text-yellow-700">
        pending override
      </Badge>
    );
  }
  if (pending.mode === "reset") {
    return (
      <Badge variant="outline" className="text-[10px] bg-yellow-500/10 border-yellow-500/30 text-yellow-700">
        pending reset
      </Badge>
    );
  }
  if (source === "tenant_policy") {
    return (
      <Badge variant="outline" className="text-[10px] bg-blue-500/10 border-blue-500/30 text-blue-700">
        override
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px] text-muted-foreground">
      env default
    </Badge>
  );
}
