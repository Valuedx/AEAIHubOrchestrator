/**
 * MCP-02 — CRUD UI for the tenant_mcp_servers registry.
 *
 * Simpler than IntegrationsDialog because an MCP server is just
 * (url, auth_mode, headers) — no AutomationEdge-specific shape. Rows
 * marked ``is_default`` are picked when an MCP Tool node leaves
 * ``mcpServerLabel`` blank; if no default is set, the backend falls
 * back to the legacy ``settings.mcp_server_url`` env var.
 *
 * Static-header values may embed ``{{ env.KEY }}`` placeholders that
 * resolve through the Fernet-encrypted Secrets vault at call time.
 * Raw tokens never live in this registry.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft,
  Globe,
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
  type McpAuthMode,
  type TenantMcpServerCreate,
  type TenantMcpServerOut,
} from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type View = "list" | "form";

interface FormState {
  label: string;
  url: string;
  auth_mode: McpAuthMode;
  headersText: string;
  is_default: boolean;
}

const EMPTY_FORM: FormState = {
  label: "",
  url: "",
  auth_mode: "none",
  headersText: "",
  is_default: false,
};


export function McpServersDialog({ open, onOpenChange }: Props) {
  const [view, setView] = useState<View>("list");
  const [rows, setRows] = useState<TenantMcpServerOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<TenantMcpServerOut | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    api
      .listMcpServers()
      .then(setRows)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open) return;
    setView("list");
    setEditing(null);
    setForm(EMPTY_FORM);
    setSaveErr(null);
    refresh();
  }, [open, refresh]);

  const startCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setSaveErr(null);
    setView("form");
  };

  const startEdit = (row: TenantMcpServerOut) => {
    setEditing(row);
    const headers =
      row.auth_mode === "static_headers"
        ? (row.config_json?.headers as Record<string, string> | undefined) ?? {}
        : {};
    setForm({
      label: row.label,
      url: row.url,
      auth_mode: row.auth_mode,
      headersText: formatHeaders(headers),
      is_default: row.is_default,
    });
    setSaveErr(null);
    setView("form");
  };

  const handleDelete = async (row: TenantMcpServerOut) => {
    if (
      !confirm(
        `Delete MCP server "${row.label}"? Nodes that reference it by label will stop resolving; nodes using the default may fall back to the env-var server.`,
      )
    )
      return;
    await api.deleteMcpServer(row.id);
    refresh();
  };

  const handleSave = async () => {
    setSaveErr(null);
    const label = form.label.trim();
    const url = form.url.trim();
    if (!label || !url) {
      setSaveErr("Label and URL are required.");
      return;
    }

    let config: Record<string, unknown> = {};
    if (form.auth_mode === "static_headers") {
      try {
        const headers = parseHeaders(form.headersText);
        config = { headers };
      } catch (e) {
        setSaveErr(String((e as Error).message || e));
        return;
      }
    }

    const body: TenantMcpServerCreate = {
      label,
      url,
      auth_mode: form.auth_mode,
      config_json: config,
      is_default: form.is_default,
    };

    try {
      if (editing) {
        await api.updateMcpServer(editing.id, body);
      } else {
        await api.createMcpServer(body);
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
            <Globe className="h-4 w-4" />
            {view === "list"
              ? "MCP Servers"
              : editing
                ? `Edit ${editing.label}`
                : "Add MCP Server"}
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
                <p>No MCP servers registered for this tenant yet.</p>
                <p className="text-[11px]">
                  The orchestrator will fall back to the
                  <code className="mx-1 text-[10px] bg-muted px-1 py-0.5 rounded">MCP_SERVER_URL</code>
                  env var for every MCP Tool node until you add one.
                </p>
              </div>
            ) : (
              <ScrollArea className="max-h-[400px]">
                <div className="space-y-1 p-1">
                  {rows.map((row) => (
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
                          <span className="text-[10px] px-1.5 py-0 rounded border text-muted-foreground shrink-0">
                            {row.auth_mode}
                          </span>
                        </div>
                        <p className="text-[11px] text-muted-foreground truncate">
                          {row.url}
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
                  ))}
                </div>
              </ScrollArea>
            )}
            <div className="flex justify-end px-1 pt-2">
              <Button variant="default" size="sm" onClick={startCreate} className="gap-1.5">
                <Plus className="h-4 w-4" /> Add server
              </Button>
            </div>
          </>
        )}

        {view === "form" && (
          <ScrollArea className="max-h-[500px]">
            <div className="space-y-3 p-1">
              <div className="space-y-1.5">
                <Label htmlFor="mcp-label">Label</Label>
                <Input
                  id="mcp-label"
                  value={form.label}
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                  placeholder="github-mcp"
                />
                <p className="text-[11px] text-muted-foreground">
                  Used to reference this server from an MCP Tool node's
                  <code className="mx-1 text-[10px] bg-muted px-1 py-0.5 rounded">mcpServerLabel</code>
                  field.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="mcp-url">URL</Label>
                <Input
                  id="mcp-url"
                  value={form.url}
                  onChange={(e) => setForm({ ...form, url: e.target.value })}
                  placeholder="https://mcp.example.com/mcp"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="mcp-auth">Auth mode</Label>
                <select
                  id="mcp-auth"
                  value={form.auth_mode}
                  onChange={(e) =>
                    setForm({ ...form, auth_mode: e.target.value as McpAuthMode })
                  }
                  className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="none">None</option>
                  <option value="static_headers">Static headers</option>
                  <option value="oauth_2_1" disabled>
                    OAuth 2.1 (coming in MCP-03)
                  </option>
                </select>
              </div>

              {form.auth_mode === "static_headers" && (
                <div className="space-y-1.5">
                  <Label htmlFor="mcp-headers">
                    Headers (one per line, <code>Name: value</code>)
                  </Label>
                  <textarea
                    id="mcp-headers"
                    value={form.headersText}
                    onChange={(e) =>
                      setForm({ ...form, headersText: e.target.value })
                    }
                    placeholder={
                      "Authorization: Bearer {{ env.MY_TOKEN }}\nX-Org: acme"
                    }
                    rows={5}
                    className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs font-mono"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    <code>{"{{ env.KEY }}"}</code> placeholders resolve through
                    the Secrets vault at call time — raw tokens never live here.
                  </p>
                </div>
              )}

              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={(e) =>
                    setForm({ ...form, is_default: e.target.checked })
                  }
                  className="rounded border-input"
                />
                Use as default for MCP Tool nodes with no label
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


// ---------------------------------------------------------------------------
// Header text-block ⇄ dict helpers
// ---------------------------------------------------------------------------

function formatHeaders(headers: Record<string, string>): string {
  return Object.entries(headers)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");
}

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const idx = line.indexOf(":");
    if (idx < 1) {
      throw new Error(`Malformed header line: ${line}`);
    }
    const name = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (!name) throw new Error(`Header missing name: ${line}`);
    out[name] = value;
  }
  return out;
}
