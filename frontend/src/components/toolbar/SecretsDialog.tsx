import { useCallback, useEffect, useState } from "react";
import {
  Trash2,
  Loader2,
  Plus,
  KeyRound,
  Eye,
  EyeOff,
  Pencil,
  ChevronLeft,
  Copy,
  Check,
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
import { api, type SecretOut } from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type View = "list" | "create" | "edit";

export function SecretsDialog({ open, onOpenChange }: Props) {
  const [view, setView] = useState<View>("list");
  const [secrets, setSecrets] = useState<SecretOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingSecret, setEditingSecret] = useState<SecretOut | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    api.listSecrets().then(setSecrets).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (open) {
      refresh();
      setView("list");
    }
  }, [open, refresh]);

  const handleDelete = (id: string, keyName: string) => {
    if (confirm(`Delete secret "${keyName}"? Any node configs using {{ env.${keyName} }} will stop resolving.`)) {
      api.deleteSecret(id).then(refresh);
    }
  };

  const openEdit = (secret: SecretOut) => {
    setEditingSecret(secret);
    setView("edit");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view !== "list" && (
              <button
                onClick={() => { setView("list"); refresh(); }}
                className="p-1 hover:bg-accent rounded"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            )}
            <KeyRound className="h-5 w-5" />
            {view === "list" && "Secrets"}
            {view === "create" && "Add Secret"}
            {view === "edit" && `Update ${editingSecret?.key_name ?? "Secret"}`}
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {view === "list" && (
          <SecretListView
            secrets={secrets}
            loading={loading}
            onDelete={handleDelete}
            onEdit={openEdit}
            onCreate={() => setView("create")}
          />
        )}

        {view === "create" && (
          <SecretCreateView
            onCreated={() => { setView("list"); refresh(); }}
          />
        )}

        {view === "edit" && editingSecret && (
          <SecretEditView
            secret={editingSecret}
            onUpdated={() => { setView("list"); refresh(); }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ---------- LIST VIEW ---------- */

function SecretListView({
  secrets,
  loading,
  onDelete,
  onEdit,
  onCreate,
}: {
  secrets: SecretOut[];
  loading: boolean;
  onDelete: (id: string, keyName: string) => void;
  onEdit: (secret: SecretOut) => void;
  onCreate: () => void;
}) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const copyRef = (keyName: string) => {
    navigator.clipboard.writeText(`{{ env.${keyName} }}`);
    setCopiedKey(keyName);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  return (
    <>
      <div className="flex justify-end">
        <Button size="sm" onClick={onCreate} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Add Secret
        </Button>
      </div>
      <ScrollArea className="flex-1 min-h-0 max-h-[55vh]">
        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!loading && secrets.length === 0 && (
          <div className="text-center py-8 space-y-2">
            <KeyRound className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              No secrets yet. Add API keys and credentials here.
            </p>
            <p className="text-xs text-muted-foreground">
              Reference them in node configs as{" "}
              <code className="bg-muted px-1.5 py-0.5 rounded text-[11px]">
                {"{{ env.KEY_NAME }}"}
              </code>
            </p>
          </div>
        )}
        <div className="space-y-2">
          {secrets.map((s) => (
            <div
              key={s.id}
              className="flex items-center gap-3 rounded-lg border px-4 py-3"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium font-mono">{s.key_name}</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[10px] text-muted-foreground">
                    Updated {new Date(s.updated_at).toLocaleDateString()}
                  </span>
                  <button
                    onClick={() => copyRef(s.key_name)}
                    className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                    title={`Copy {{ env.${s.key_name} }}`}
                  >
                    {copiedKey === s.key_name ? (
                      <Check className="h-3 w-3 text-green-500" />
                    ) : (
                      <Copy className="h-3 w-3" />
                    )}
                    <code className="bg-muted px-1 py-0.5 rounded">
                      {"{{ env." + s.key_name + " }}"}
                    </code>
                  </button>
                </div>
              </div>
              <button
                onClick={() => onEdit(s)}
                className="p-1.5 text-muted-foreground hover:text-foreground transition-colors shrink-0"
                title="Update value"
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => onDelete(s.id, s.key_name)}
                className="p-1.5 text-muted-foreground hover:text-red-500 transition-colors shrink-0"
                title="Delete"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </ScrollArea>
    </>
  );
}

/* ---------- CREATE VIEW ---------- */

function SecretCreateView({ onCreated }: { onCreated: () => void }) {
  const [keyName, setKeyName] = useState("");
  const [value, setValue] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const keyNameValid = /^\w+$/.test(keyName);

  const handleSubmit = async () => {
    if (!keyName.trim() || !value.trim()) return;
    if (!keyNameValid) {
      setError("Key name must contain only letters, digits, and underscores");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api.createSecret(keyName.trim(), value);
      onCreated();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to create secret";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Key Name</Label>
        <Input
          value={keyName}
          onChange={(e) => setKeyName(e.target.value)}
          placeholder="OPENAI_API_KEY"
          className="font-mono"
          autoFocus
        />
        <p className="text-[10px] text-muted-foreground">
          Letters, digits, and underscores only. Use as{" "}
          <code className="bg-muted px-1 py-0.5 rounded">
            {"{{ env." + (keyName || "KEY_NAME") + " }}"}
          </code>{" "}
          in node configs.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Value</Label>
        <div className="relative">
          <Input
            type={showValue ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="sk-..."
            className="pr-10 font-mono"
          />
          <button
            type="button"
            onClick={() => setShowValue(!showValue)}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground"
          >
            {showValue ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <p className="text-[10px] text-muted-foreground">
          Encrypted at rest. Cannot be viewed after saving.
        </p>
      </div>

      {error && (
        <p className="text-sm text-red-500">{error}</p>
      )}

      <div className="flex justify-end pt-2">
        <Button
          onClick={handleSubmit}
          disabled={!keyName.trim() || !value.trim() || saving}
          className="gap-1.5"
        >
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          Save Secret
        </Button>
      </div>
    </div>
  );
}

/* ---------- EDIT VIEW ---------- */

function SecretEditView({
  secret,
  onUpdated,
}: {
  secret: SecretOut;
  onUpdated: () => void;
}) {
  const [value, setValue] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async () => {
    if (!value.trim()) return;
    setSaving(true);
    setError("");
    try {
      await api.updateSecret(secret.id, value);
      onUpdated();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to update secret";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Key Name</Label>
        <Input
          value={secret.key_name}
          disabled
          className="font-mono bg-muted"
        />
      </div>

      <div className="space-y-2">
        <Label>New Value</Label>
        <div className="relative">
          <Input
            type={showValue ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Enter new value..."
            className="pr-10 font-mono"
            autoFocus
          />
          <button
            type="button"
            onClick={() => setShowValue(!showValue)}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground"
          >
            {showValue ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <p className="text-[10px] text-muted-foreground">
          The old value cannot be retrieved. Enter the complete new value.
        </p>
      </div>

      {error && (
        <p className="text-sm text-red-500">{error}</p>
      )}

      <div className="flex justify-end pt-2">
        <Button
          onClick={handleSubmit}
          disabled={!value.trim() || saving}
          className="gap-1.5"
        >
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          Update Secret
        </Button>
      </div>
    </div>
  );
}
