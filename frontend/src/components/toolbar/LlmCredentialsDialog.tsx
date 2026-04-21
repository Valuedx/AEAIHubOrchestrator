/**
 * ADMIN-03 — per-tenant LLM provider credentials dialog.
 *
 * Specialised view over the existing ``tenant_secrets`` vault that
 * writes four well-known keys: LLM_GOOGLE_API_KEY, LLM_OPENAI_API_KEY,
 * LLM_OPENAI_BASE_URL, LLM_ANTHROPIC_API_KEY. Operators get labelled
 * fields instead of having to remember conventional secret names.
 *
 * UX model
 * --------
 *
 *   * On open: GET ``/api/v1/llm-credentials`` for per-provider source
 *     labels ("tenant_secret" / "env_default" / "missing") + GET the
 *     raw secrets list so we can look up the row id for UPDATE/DELETE.
 *     Secret *values* are never fetched — the backend refuses to
 *     return them by design.
 *   * Per field: password-masked input. Empty = leave alone. Typing
 *     any value queues a "set" action. A "Clear override" button
 *     queues a "delete" action that restores env fallback.
 *   * Save executes pending actions sequentially via the existing
 *     /api/v1/secrets CRUD endpoints.
 *
 * Sits alongside the generic SecretsDialog — that one still handles
 * custom secrets (``{{ env.MY_CUSTOM }}``); this one is a shortcut
 * for the four LLM provider keys specifically.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Eye,
  EyeOff,
  Key,
  Loader2,
  RotateCcw,
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
  type LlmCredentialSource,
  type LlmCredentialsOut,
  type SecretOut,
} from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Mirrors the backend's well-known secret names. Keep in sync with
// engine/llm_credentials_resolver.py.
const KEYS = {
  google: "LLM_GOOGLE_API_KEY",
  openai: "LLM_OPENAI_API_KEY",
  openai_base_url: "LLM_OPENAI_BASE_URL",
  anthropic: "LLM_ANTHROPIC_API_KEY",
} as const;

type ProviderKey = keyof typeof KEYS;

interface FieldMeta {
  provider: ProviderKey;
  label: string;
  description: string;
  placeholder: string;
  isSecret: boolean;
}

const FIELDS: FieldMeta[] = [
  {
    provider: "google",
    label: "Google AI Studio API key",
    description:
      "Used for provider: \"google\" Gemini nodes. Vertex AI uses a different auth path (VERTEX-02 — Cloud toolbar icon).",
    placeholder: "AIza...",
    isSecret: true,
  },
  {
    provider: "openai",
    label: "OpenAI API key",
    description:
      "Used for provider: \"openai\" nodes. OpenAI-compatible endpoints (LiteLLM, Azure OpenAI) also work — set the base URL below.",
    placeholder: "sk-...",
    isSecret: true,
  },
  {
    provider: "openai_base_url",
    label: "OpenAI base URL (optional)",
    description:
      "Custom endpoint for OpenAI-compatible APIs. Leave blank to use https://api.openai.com/v1. Not a secret but kept here for locality.",
    placeholder: "https://api.openai.com/v1",
    isSecret: false,
  },
  {
    provider: "anthropic",
    label: "Anthropic API key",
    description:
      "Used for provider: \"anthropic\" Claude nodes.",
    placeholder: "sk-ant-...",
    isSecret: true,
  },
];

// Per-field pending action. ``unchanged`` omits from Save; ``set``
// writes a new value; ``clear`` deletes the secret so env default
// applies.
type Pending = { mode: "unchanged" } | { mode: "set"; value: string } | { mode: "clear" };


export function LlmCredentialsDialog({ open, onOpenChange }: Props) {
  const [status, setStatus] = useState<LlmCredentialsOut | null>(null);
  const [secrets, setSecrets] = useState<SecretOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pending, setPending] = useState<Record<ProviderKey, Pending>>(_emptyPending);
  const [visible, setVisible] = useState<Record<ProviderKey, boolean>>({
    google: false,
    openai: false,
    openai_base_url: true, // base URL is not a secret
    anthropic: false,
  });
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [status, secrets] = await Promise.all([
        api.getLlmCredentials(),
        api.listSecrets(),
      ]);
      setStatus(status);
      setSecrets(secrets);
      setPending(_emptyPending());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
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

  const handleSet = (provider: ProviderKey, value: string) => {
    if (value === "") {
      setPending({ ...pending, [provider]: { mode: "unchanged" } });
      return;
    }
    setPending({ ...pending, [provider]: { mode: "set", value } });
  };

  const handleClear = (provider: ProviderKey) => {
    setPending({ ...pending, [provider]: { mode: "clear" } });
  };

  const handleUndo = (provider: ProviderKey) => {
    setPending({ ...pending, [provider]: { mode: "unchanged" } });
  };

  const handleSave = async () => {
    setErr(null);
    setSaving(true);
    try {
      for (const field of FIELDS) {
        const p = pending[field.provider];
        const keyName = KEYS[field.provider];
        const existing = secrets.find((s) => s.key_name === keyName);

        if (p.mode === "set") {
          if (existing) {
            await api.updateSecret(existing.id, p.value);
          } else {
            await api.createSecret(keyName, p.value);
          }
        } else if (p.mode === "clear") {
          if (existing) {
            await api.deleteSecret(existing.id);
          }
          // If no existing row, "clear" is a no-op (already at env default).
        }
      }
      await refresh();
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
            <Key className="h-4 w-4" />
            LLM Provider Credentials
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {loading || status === null ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-5 p-1">
              <p className="text-[11px] text-muted-foreground">
                Tenant-scoped keys override the <code className="text-[10px] bg-muted px-1 py-0.5 rounded">ORCHESTRATOR_*_API_KEY</code> env defaults for this tenant's workflows. Keys are stored encrypted at rest in the Secrets vault — no one (including operators) can read a saved value back.
              </p>

              {FIELDS.map((meta) => {
                const source = status.providers[meta.provider]?.source ?? "missing";
                const state = pending[meta.provider];
                return (
                  <CredentialFieldRow
                    key={meta.provider}
                    meta={meta}
                    source={source}
                    pending={state}
                    visible={visible[meta.provider]}
                    onToggleVisible={() =>
                      setVisible({ ...visible, [meta.provider]: !visible[meta.provider] })
                    }
                    onSet={(v) => handleSet(meta.provider, v)}
                    onClear={() => handleClear(meta.provider)}
                    onUndo={() => handleUndo(meta.provider)}
                  />
                );
              })}

              {err && <p className="text-xs text-destructive">{err}</p>}

              <div className="flex justify-end gap-2 pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setPending(_emptyPending())}
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
// Field row
// ---------------------------------------------------------------------------


interface RowProps {
  meta: FieldMeta;
  source: LlmCredentialSource;
  pending: Pending;
  visible: boolean;
  onToggleVisible: () => void;
  onSet: (value: string) => void;
  onClear: () => void;
  onUndo: () => void;
}

function CredentialFieldRow({
  meta,
  source,
  pending,
  visible,
  onToggleVisible,
  onSet,
  onClear,
  onUndo,
}: RowProps) {
  const inputValue = pending.mode === "set" ? pending.value : "";
  const inputType = !meta.isSecret || visible ? "text" : "password";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="font-medium">{meta.label}</Label>
        <SourcePill source={source} pending={pending} />
      </div>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Input
            type={inputType}
            value={inputValue}
            placeholder={
              source === "tenant_secret"
                ? "(already set — type to replace, or Clear to reset)"
                : meta.placeholder
            }
            onChange={(e) => onSet(e.target.value)}
            className={meta.isSecret ? "pr-8 font-mono text-xs" : ""}
          />
          {meta.isSecret && (
            <button
              type="button"
              onClick={onToggleVisible}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-accent/50"
              aria-label={visible ? "Hide" : "Show"}
            >
              {visible ? (
                <EyeOff className="h-3.5 w-3.5 text-muted-foreground" />
              ) : (
                <Eye className="h-3.5 w-3.5 text-muted-foreground" />
              )}
            </button>
          )}
        </div>
        {pending.mode !== "unchanged" && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1 text-[11px]"
            onClick={onUndo}
            title="Cancel this pending change"
          >
            <X className="h-3 w-3" /> undo
          </Button>
        )}
        {source === "tenant_secret" && pending.mode !== "clear" && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1 text-[11px] text-muted-foreground hover:text-foreground"
            onClick={onClear}
            title="Delete the tenant override — fall back to the env default"
          >
            <RotateCcw className="h-3 w-3" /> clear
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
  source: LlmCredentialSource;
  pending: Pending;
}) {
  if (pending.mode === "set") {
    return (
      <Badge variant="outline" className="text-[10px] bg-yellow-500/10 border-yellow-500/30 text-yellow-700">
        pending override
      </Badge>
    );
  }
  if (pending.mode === "clear") {
    return (
      <Badge variant="outline" className="text-[10px] bg-yellow-500/10 border-yellow-500/30 text-yellow-700">
        pending clear
      </Badge>
    );
  }
  if (source === "tenant_secret") {
    return (
      <Badge variant="outline" className="text-[10px] bg-blue-500/10 border-blue-500/30 text-blue-700">
        tenant override
      </Badge>
    );
  }
  if (source === "missing") {
    return (
      <Badge variant="outline" className="text-[10px] bg-destructive/10 border-destructive/30 text-destructive">
        not configured
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px] text-muted-foreground">
      env default
    </Badge>
  );
}


function _emptyPending(): Record<ProviderKey, Pending> {
  return {
    google: { mode: "unchanged" },
    openai: { mode: "unchanged" },
    openai_base_url: { mode: "unchanged" },
    anthropic: { mode: "unchanged" },
  };
}
