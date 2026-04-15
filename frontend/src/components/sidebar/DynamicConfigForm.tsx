/**
 * DynamicConfigForm — renders a config panel from a registry JSON schema.
 *
 * Field rendering rules (per schema entry type):
 *  - string + enum      → Select
 *  - string (systemPrompt / approvalMessage / body) → Textarea
 *  - string             → Input text
 *  - number / integer   → Input number (step 0.1 / 1, min/max from schema)
 *  - boolean            → Switch
 *  - array (tools field on react_agent) → ToolMultiSelect (live MCP list)
 *  - array (other)      → Textarea (JSON)
 *  - object             → Textarea (JSON, validated on blur)
 */

import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import type { ToolOut } from "@/lib/api";
import { ExpressionInput } from "@/components/sidebar/ExpressionInput";
import { KBMultiSelect } from "@/components/sidebar/KBMultiSelect";
import { getExpressionVariables } from "@/lib/expressionVariables";
import { useFlowStore } from "@/store/flowStore";
import { useWorkflowStore } from "@/store/workflowStore";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FieldSchema = {
  type: string;
  default?: unknown;
  enum?: unknown[];
  min?: number;
  max?: number;
  items?: { type: string };
  description?: string;
  visibleWhen?: { field: string; values: (string | boolean)[] };
};

export interface DynamicConfigFormProps {
  nodeType: string;                                    // e.g. "react_agent"
  schema: Record<string, FieldSchema>;
  config: Record<string, unknown>;
  onUpdate: (partial: { config: Record<string, unknown> }) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** camelCase → "Camel Case" */
function humanize(key: string): string {
  return key
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (c) => c.toUpperCase())
    .trim();
}

const TEXTAREA_KEYS = new Set(["systemPrompt", "approvalMessage", "body"]);

// Fields that accept safe_eval dot-path expressions (e.g. node_2.intent == "x")
const EXPRESSION_KEYS = new Set([
  "condition",
  "arrayExpression",
  "continueExpression",
  "sessionIdExpression",
  "userMessageExpression",
  "messageExpression",
  "queryExpression",
  "destination",
  "chatId",
  "phoneNumber",
  "to",
  "smtpUser",
  "smtpPass",
  "utteranceExpression",
  "sourceExpression",
]);

// Fields that accept a bare node ID (e.g. node_3)
const NODE_ID_KEYS = new Set(["responseNodeId", "historyNodeId", "scopeFromNode"]);

// Fields that accept Jinja2 templates (e.g. {{ trigger.message }})
const JINJA2_KEYS = new Set(["systemPrompt", "messageTemplate", "subject"]);

// ---------------------------------------------------------------------------
// FieldHint — grey help text rendered below a field input
// ---------------------------------------------------------------------------

function FieldHint({ text }: { text: string }) {
  return (
    <p className="text-[10px] text-muted-foreground leading-snug">{text}</p>
  );
}

// ---------------------------------------------------------------------------
// JsonTextarea — controlled JSON editor that syncs when the serialised value
// changes externally (e.g. after an undo/redo that replaces config).
// ---------------------------------------------------------------------------

function JsonTextarea({
  id,
  rows,
  canonicalValue,   // the authoritative value from the store (as a JS object)
  onCommit,         // called with the parsed object on valid blur
}: {
  id: string;
  rows: number;
  canonicalValue: unknown;
  onCommit: (value: unknown) => void;
}) {
  const [raw, setRaw] = useState(() => JSON.stringify(canonicalValue, null, 2));
  const [hasError, setHasError] = useState(false);
  // Track the last canonical JSON string so we can detect external changes
  const lastCanonical = useRef(JSON.stringify(canonicalValue));

  // When the store value changes (e.g. undo/redo), reset the local raw string
  const canonical = JSON.stringify(canonicalValue);
  if (canonical !== lastCanonical.current) {
    lastCanonical.current = canonical;
    setRaw(JSON.stringify(canonicalValue, null, 2));
    setHasError(false);
  }

  const handleBlur = () => {
    try {
      onCommit(JSON.parse(raw));
      setHasError(false);
    } catch {
      setHasError(true);
    }
  };

  return (
    <>
      <Textarea
        id={id}
        rows={rows}
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        onBlur={handleBlur}
        className={hasError ? "border-red-500" : ""}
      />
      {hasError && <p className="text-[10px] text-red-500">Invalid JSON</p>}
    </>
  );
}

// ---------------------------------------------------------------------------
// CodeTextarea — monospace editor with tab support for code_execution nodes
// ---------------------------------------------------------------------------

function CodeTextarea({
  value,
  onChange,
  placeholder,
  description,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  description?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const cursorRef = useRef<number | null>(null);

  useEffect(() => {
    if (cursorRef.current != null && ref.current) {
      ref.current.selectionStart = ref.current.selectionEnd = cursorRef.current;
      cursorRef.current = null;
    }
  });

  return (
    <div className="space-y-2">
      <Label>Code</Label>
      <textarea
        ref={ref}
        rows={12}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Tab") {
            e.preventDefault();
            const el = e.currentTarget;
            const start = el.selectionStart;
            const end = el.selectionEnd;
            const updated = el.value.substring(0, start) + "  " + el.value.substring(end);
            cursorRef.current = start + 2;
            onChange(updated);
          }
        }}
        spellCheck={false}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono leading-relaxed placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y min-h-[200px]"
        style={{ tabSize: 2 }}
        placeholder={placeholder}
      />
      {description && <FieldHint text={description} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool multi-select sub-component (for react_agent tools field)
// ---------------------------------------------------------------------------

function ToolMultiSelect({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (names: string[]) => void;
}) {
  const [tools, setTools] = useState<ToolOut[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listTools().then((ts) => {
      setTools(ts);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-xs text-muted-foreground">Loading tools…</p>;
  }

  if (tools.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No tools available (MCP server may be offline).
        Leave empty to auto-discover all tools at runtime.
      </p>
    );
  }

  const toggleTool = (name: string) => {
    if (selected.includes(name)) {
      onChange(selected.filter((n) => n !== name));
    } else {
      onChange([...selected, name]);
    }
  };

  // Group by category
  const byCategory: Record<string, ToolOut[]> = {};
  for (const t of tools) {
    (byCategory[t.category] ??= []).push(t);
  }

  return (
    <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
      {selected.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          None selected — all tools auto-discovered at runtime
        </p>
      )}
      {Object.entries(byCategory).map(([cat, catTools]) => (
        <div key={cat} className="space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {cat}
          </p>
          {catTools.map((t) => {
            const checked = selected.includes(t.name);
            return (
              <label
                key={t.name}
                className="flex items-start gap-2 cursor-pointer group"
              >
                <input
                  type="checkbox"
                  className="mt-0.5 shrink-0"
                  checked={checked}
                  onChange={() => toggleTool(t.name)}
                />
                <div className="min-w-0">
                  <span className={`text-xs ${checked ? "text-foreground" : "text-muted-foreground"} group-hover:text-foreground transition-colors`}>
                    {t.title || t.name}
                  </span>
                  <Badge
                    variant="outline"
                    className="ml-1.5 text-[9px] px-1 py-0"
                  >
                    {t.safety_tier}
                  </Badge>
                </div>
              </label>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool single-select sub-component (for mcp_tool toolName field)
// ---------------------------------------------------------------------------

function ToolSingleSelect({
  selected,
  onChange,
}: {
  selected: string;
  onChange: (name: string) => void;
}) {
  const [tools, setTools] = useState<ToolOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api.listTools().then((ts) => {
      setTools(ts);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-xs text-muted-foreground">Loading tools…</p>;
  }

  if (tools.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No tools available (MCP server may be offline). Type the tool name manually below.
      </p>
    );
  }

  const filtered = search.trim()
    ? tools.filter(
        (t) =>
          t.name.toLowerCase().includes(search.toLowerCase()) ||
          (t.title || "").toLowerCase().includes(search.toLowerCase()) ||
          (t.description || "").toLowerCase().includes(search.toLowerCase()),
      )
    : tools;

  // Group by category
  const byCategory: Record<string, ToolOut[]> = {};
  for (const t of filtered) {
    (byCategory[t.category] ??= []).push(t);
  }

  return (
    <div className="space-y-2">
      {/* Search filter */}
      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Filter tools…"
        className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />

      {/* Selected indicator */}
      {selected && (
        <div className="flex items-center gap-1.5 rounded-md bg-primary/10 border border-primary/20 px-2.5 py-1.5">
          <span className="text-xs font-mono text-primary truncate flex-1">{selected}</span>
          <button
            onClick={() => onChange("")}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0"
            title="Clear selection"
          >
            ✕
          </button>
        </div>
      )}

      {/* Tool list */}
      <div className="max-h-52 overflow-y-auto space-y-1 pr-1">
        {Object.entries(byCategory).map(([cat, catTools]) => (
          <div key={cat} className="space-y-0.5">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground px-1 pt-1">
              {cat}
            </p>
            {catTools.map((t) => {
              const isSelected = selected === t.name;
              return (
                <button
                  key={t.name}
                  onClick={() => onChange(isSelected ? "" : t.name)}
                  className={`w-full text-left rounded-md px-2.5 py-2 transition-colors group ${
                    isSelected
                      ? "bg-primary/10 border border-primary/30"
                      : "hover:bg-accent border border-transparent"
                  }`}
                >
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className={`text-xs font-medium truncate flex-1 ${isSelected ? "text-primary" : "text-foreground"}`}>
                      {t.title || t.name}
                    </span>
                    <Badge variant="outline" className="text-[9px] px-1 py-0 shrink-0">
                      {t.safety_tier}
                    </Badge>
                  </div>
                  {t.description && (
                    <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-2 text-left">
                      {t.description}
                    </p>
                  )}
                  <p className="text-[9px] font-mono text-muted-foreground/70 mt-0.5">{t.name}</p>
                </button>
              );
            })}
          </div>
        ))}
        {filtered.length === 0 && (
          <p className="text-xs text-muted-foreground py-2 px-1">No tools match "{search}"</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IntentListEditor — array-of-objects editor for Intent Classifier intents
// ---------------------------------------------------------------------------

interface IntentItem {
  name: string;
  description: string;
  examples: string[];
  priority: number;
}

function IntentListEditor({
  value,
  onChange,
  description,
}: {
  value: IntentItem[];
  onChange: (v: IntentItem[]) => void;
  description?: string;
}) {
  const items: IntentItem[] = Array.isArray(value) ? value : [];

  const addItem = () => {
    onChange([...items, { name: "", description: "", examples: [], priority: 100 }]);
  };

  const removeItem = (idx: number) => {
    onChange(items.filter((_, i) => i !== idx));
  };

  const updateItem = (idx: number, patch: Partial<IntentItem>) => {
    const updated = items.map((it, i) => (i === idx ? { ...it, ...patch } : it));
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Intents</Label>
        <button
          type="button"
          onClick={addItem}
          className="text-xs text-primary hover:underline"
        >
          + Add intent
        </button>
      </div>
      {items.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          No intents configured — click &quot;+ Add intent&quot; to start
        </p>
      )}
      {items.map((intent, idx) => (
        <div
          key={idx}
          className="border rounded-md p-3 space-y-2 bg-muted/30"
        >
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
              Intent {idx + 1}
            </span>
            <button
              type="button"
              onClick={() => removeItem(idx)}
              className="text-[10px] text-destructive hover:underline"
            >
              Remove
            </button>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`intent-name-${idx}`} className="text-[11px]">Name *</Label>
            <Input
              id={`intent-name-${idx}`}
              value={intent.name}
              onChange={(e) => updateItem(idx, { name: e.target.value })}
              placeholder="e.g. book_flight"
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`intent-desc-${idx}`} className="text-[11px]">Description</Label>
            <Input
              id={`intent-desc-${idx}`}
              value={intent.description}
              onChange={(e) => updateItem(idx, { description: e.target.value })}
              placeholder="User wants to book a flight"
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`intent-ex-${idx}`} className="text-[11px]">Examples (comma-separated)</Label>
            <Input
              id={`intent-ex-${idx}`}
              value={(intent.examples || []).join(", ")}
              onChange={(e) =>
                updateItem(idx, {
                  examples: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
              placeholder="book a flight, fly to, I need tickets"
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`intent-pri-${idx}`} className="text-[11px]">Priority</Label>
            <Input
              id={`intent-pri-${idx}`}
              type="number"
              value={intent.priority ?? 100}
              onChange={(e) => updateItem(idx, { priority: parseInt(e.target.value, 10) || 100 })}
              className="h-8 text-xs w-24"
            />
          </div>
        </div>
      ))}
      {description && <FieldHint text={description} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EntityListEditor — array-of-objects editor for Entity Extractor entities
// ---------------------------------------------------------------------------

interface EntityItem {
  name: string;
  type: string;
  pattern: string;
  enum_values: string[];
  description: string;
  required: boolean;
}

const ENTITY_TYPES = ["regex", "enum", "number", "date", "free_text"] as const;

function EntityListEditor({
  value,
  onChange,
  description,
}: {
  value: EntityItem[];
  onChange: (v: EntityItem[]) => void;
  description?: string;
}) {
  const items: EntityItem[] = Array.isArray(value) ? value : [];

  const addItem = () => {
    onChange([
      ...items,
      { name: "", type: "free_text", pattern: "", enum_values: [], description: "", required: false },
    ]);
  };

  const removeItem = (idx: number) => {
    onChange(items.filter((_, i) => i !== idx));
  };

  const updateItem = (idx: number, patch: Partial<EntityItem>) => {
    const updated = items.map((it, i) => (i === idx ? { ...it, ...patch } : it));
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Entities</Label>
        <button
          type="button"
          onClick={addItem}
          className="text-xs text-primary hover:underline"
        >
          + Add entity
        </button>
      </div>
      {items.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          No entities configured — click &quot;+ Add entity&quot; to start
        </p>
      )}
      {items.map((entity, idx) => (
        <div
          key={idx}
          className="border rounded-md p-3 space-y-2 bg-muted/30"
        >
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
              Entity {idx + 1}
            </span>
            <button
              type="button"
              onClick={() => removeItem(idx)}
              className="text-[10px] text-destructive hover:underline"
            >
              Remove
            </button>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`ent-name-${idx}`} className="text-[11px]">Name *</Label>
            <Input
              id={`ent-name-${idx}`}
              value={entity.name}
              onChange={(e) => updateItem(idx, { name: e.target.value })}
              placeholder="e.g. destination"
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`ent-type-${idx}`} className="text-[11px]">Type</Label>
            <Select
              value={entity.type || "free_text"}
              onValueChange={(v) => updateItem(idx, { type: v })}
            >
              <SelectTrigger id={`ent-type-${idx}`} className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENTITY_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {entity.type === "regex" && (
            <div className="space-y-1">
              <Label htmlFor={`ent-pat-${idx}`} className="text-[11px]">Pattern (regex)</Label>
              <Input
                id={`ent-pat-${idx}`}
                value={entity.pattern}
                onChange={(e) => updateItem(idx, { pattern: e.target.value })}
                placeholder="e.g. (\d{3}-\d{4})"
                className="h-8 text-xs font-mono"
              />
            </div>
          )}
          {entity.type === "enum" && (
            <div className="space-y-1">
              <Label htmlFor={`ent-enum-${idx}`} className="text-[11px]">Values (comma-separated)</Label>
              <Input
                id={`ent-enum-${idx}`}
                value={(entity.enum_values || []).join(", ")}
                onChange={(e) =>
                  updateItem(idx, {
                    enum_values: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="economy, business, first"
                className="h-8 text-xs"
              />
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor={`ent-desc-${idx}`} className="text-[11px]">Description</Label>
            <Input
              id={`ent-desc-${idx}`}
              value={entity.description}
              onChange={(e) => updateItem(idx, { description: e.target.value })}
              placeholder="The travel destination city"
              className="h-8 text-xs"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              id={`ent-req-${idx}`}
              type="checkbox"
              checked={entity.required ?? false}
              onChange={(e) => updateItem(idx, { required: e.target.checked })}
            />
            <Label htmlFor={`ent-req-${idx}`} className="text-[11px]">Required</Label>
          </div>
        </div>
      ))}
      {description && <FieldHint text={description} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// WorkflowSelect — searchable dropdown for sub_workflow workflowId field
// ---------------------------------------------------------------------------

function WorkflowSelect({
  selected,
  onChange,
  excludeId,
}: {
  selected: string;
  onChange: (id: string) => void;
  excludeId?: string | null;
}) {
  const [workflows, setWorkflows] = useState<{ id: string; name: string; version: number }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listWorkflows().then((wfs) => {
      setWorkflows(wfs.map((w) => ({ id: w.id, name: w.name, version: w.version })));
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-xs text-muted-foreground">Loading workflows...</p>;
  }

  const available = workflows.filter((w) => w.id !== excludeId);

  if (available.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No other workflows available. Create and save a workflow first.
      </p>
    );
  }

  const selectedWf = available.find((w) => w.id === selected);

  return (
    <div className="space-y-1.5">
      {selectedWf && (
        <div className="flex items-center gap-1.5 rounded-md bg-primary/10 border border-primary/20 px-2.5 py-1.5">
          <span className="text-xs text-primary truncate flex-1">{selectedWf.name}</span>
          <Badge variant="outline" className="text-[9px] px-1 py-0 shrink-0">
            v{selectedWf.version}
          </Badge>
          <button
            onClick={() => onChange("")}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0"
            title="Clear selection"
          >
            ✕
          </button>
        </div>
      )}
      <Select
        value={selected || "__none__"}
        onValueChange={(v) => onChange(v === "__none__" ? "" : v)}
      >
        <SelectTrigger className="text-xs">
          <SelectValue placeholder="Select a workflow..." />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__none__">— None —</SelectItem>
          {available.map((w) => (
            <SelectItem key={w.id} value={w.id}>
              {w.name} (v{w.version})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// InputMappingEditor — key-value pair editor for sub_workflow inputMapping
// ---------------------------------------------------------------------------

function InputMappingEditor({
  value,
  onChange,
  exprSuggestions,
  description,
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  exprSuggestions: { label: string; value: string }[];
  description?: string;
}) {
  const entries = Object.entries(value || {});

  const addEntry = () => {
    onChange({ ...value, "": "" });
  };

  const removeEntry = (oldKey: string) => {
    const next = { ...value };
    delete next[oldKey];
    onChange(next);
  };

  const updateKey = (oldKey: string, newKey: string) => {
    const next: Record<string, string> = {};
    for (const [k, v] of Object.entries(value || {})) {
      next[k === oldKey ? newKey : k] = v;
    }
    onChange(next);
  };

  const updateValue = (key: string, newVal: string) => {
    onChange({ ...value, [key]: newVal });
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Input Mapping</Label>
        <button
          type="button"
          onClick={addEntry}
          className="text-xs text-primary hover:underline"
        >
          + Add mapping
        </button>
      </div>
      {entries.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          No input mappings — the child workflow will receive an empty trigger
        </p>
      )}
      {entries.map(([key, expr], idx) => (
        <div key={idx} className="flex items-start gap-2 border rounded-md p-2 bg-muted/30">
          <div className="flex-1 space-y-1">
            <Input
              value={key}
              onChange={(e) => updateKey(key, e.target.value)}
              placeholder="trigger key"
              className="h-7 text-xs font-mono"
            />
            <ExpressionInput
              value={String(expr || "")}
              onChange={(v) => updateValue(key, v)}
              suggestions={exprSuggestions}
              placeholder="e.g. node_2.response"
            />
          </div>
          <button
            type="button"
            onClick={() => removeEntry(key)}
            className="text-xs text-destructive hover:underline mt-1 shrink-0"
          >
            ✕
          </button>
        </div>
      ))}
      {description && <FieldHint text={description} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OutputNodePicker — checkbox list from child workflow's graph nodes
// ---------------------------------------------------------------------------

function OutputNodePicker({
  workflowId,
  selected,
  onChange,
  description,
}: {
  workflowId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
  description?: string;
}) {
  const [childNodes, setChildNodes] = useState<{ id: string; label: string }[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!workflowId) {
      setChildNodes([]);
      return;
    }
    setLoading(true);
    api.getWorkflow(workflowId).then((wf) => {
      const nodes = (wf.graph_json?.nodes || []) as { id: string; data?: { label?: string; displayName?: string } }[];
      setChildNodes(
        nodes.map((n) => ({
          id: n.id,
          label: n.data?.displayName || n.data?.label || n.id,
        }))
      );
      setLoading(false);
    }).catch(() => {
      setChildNodes([]);
      setLoading(false);
    });
  }, [workflowId]);

  if (!workflowId) return null;
  if (loading) return <p className="text-xs text-muted-foreground">Loading child nodes...</p>;
  if (childNodes.length === 0) return null;

  const toggle = (id: string) => {
    if (selected.includes(id)) {
      onChange(selected.filter((x) => x !== id));
    } else {
      onChange([...selected, id]);
    }
  };

  return (
    <div className="space-y-2">
      <Label>Output Node Filter</Label>
      {selected.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          None selected — all child node outputs will be returned
        </p>
      )}
      <div className="max-h-40 overflow-y-auto space-y-1 pr-1">
        {childNodes.map((n) => (
          <label key={n.id} className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={selected.includes(n.id)}
              onChange={() => toggle(n.id)}
            />
            <span className="text-xs">{n.label}</span>
            <span className="text-[9px] text-muted-foreground font-mono">{n.id}</span>
          </label>
        ))}
      </div>
      {description && <FieldHint text={description} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DynamicConfigForm({
  nodeType,
  schema,
  config,
  onUpdate,
}: DynamicConfigFormProps) {

  // Expression autocomplete — build variable suggestions from canvas state
  const nodes = useFlowStore((s) => s.nodes);
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const currentWorkflowId = useWorkflowStore((s) => s.currentWorkflow?.id ?? null);
  const exprSuggestions = getExpressionVariables(nodes, selectedNodeId, "expression");
  const nodeIdSuggestions = getExpressionVariables(nodes, selectedNodeId, "nodeId");
  const jinja2Suggestions = getExpressionVariables(nodes, selectedNodeId, "jinja2");

  const update = (key: string, value: unknown) => {
    onUpdate({ config: { ...config, [key]: value } });
  };

  return (
    <div className="space-y-4">
      {Object.entries(schema).map(([key, field]) => {
        if (field.visibleWhen) {
          const depValue = config[field.visibleWhen.field];
          if (!field.visibleWhen.values.includes(depValue as string | boolean)) {
            return null;
          }
        }

        const value = config[key];

        // ---- enum → Select ----
        if (field.enum && field.enum.length > 0) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <Select
                value={String(value ?? field.default ?? "")}
                onValueChange={(v) => update(key, v)}
              >
                <SelectTrigger id={key}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {field.enum.map((opt) => (
                    <SelectItem key={String(opt)} value={String(opt)}>
                      {String(opt)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- tools array on react_agent → ToolMultiSelect ----
        if (field.type === "array" && key === "tools" && nodeType === "react_agent") {
          const selected = Array.isArray(value) ? (value as string[]) : [];
          return (
            <div key={key} className="space-y-2">
              <Label>{humanize(key)}</Label>
              <ToolMultiSelect
                selected={selected}
                onChange={(names) => update(key, names)}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- knowledgeBaseIds on knowledge_retrieval → KBMultiSelect ----
        if (field.type === "array" && key === "knowledgeBaseIds" && nodeType === "knowledge_retrieval") {
          const selected = Array.isArray(value) ? (value as string[]) : [];
          return (
            <div key={key} className="space-y-2">
              <Label>{humanize(key)}</Label>
              <KBMultiSelect
                selected={selected}
                onChange={(ids) => update(key, ids)}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- intents array on intent_classifier → IntentListEditor ----
        if (field.type === "array" && key === "intents" && nodeType === "intent_classifier") {
          return (
            <div key={key}>
              <IntentListEditor
                value={Array.isArray(value) ? (value as IntentItem[]) : []}
                onChange={(v) => update(key, v)}
                description={field.description}
              />
            </div>
          );
        }

        // ---- entities array on entity_extractor → EntityListEditor ----
        if (field.type === "array" && key === "entities" && nodeType === "entity_extractor") {
          return (
            <div key={key}>
              <EntityListEditor
                value={Array.isArray(value) ? (value as EntityItem[]) : []}
                onChange={(v) => update(key, v)}
                description={field.description}
              />
            </div>
          );
        }

        // ---- workflowId on sub_workflow → WorkflowSelect ----
        if (field.type === "string" && key === "workflowId" && nodeType === "sub_workflow") {
          return (
            <div key={key} className="space-y-2">
              <Label>{humanize(key)}</Label>
              <WorkflowSelect
                selected={String(value ?? "")}
                onChange={(id) => update(key, id)}
                excludeId={currentWorkflowId}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- inputMapping on sub_workflow → InputMappingEditor ----
        if (field.type === "object" && key === "inputMapping" && nodeType === "sub_workflow") {
          return (
            <div key={key}>
              <InputMappingEditor
                value={(value as Record<string, string>) ?? {}}
                onChange={(v) => update(key, v)}
                exprSuggestions={exprSuggestions}
                description={field.description}
              />
            </div>
          );
        }

        // ---- outputNodeIds on sub_workflow → OutputNodePicker ----
        if (field.type === "array" && key === "outputNodeIds" && nodeType === "sub_workflow") {
          const wfId = String(config["workflowId"] ?? "");
          return (
            <div key={key}>
              <OutputNodePicker
                workflowId={wfId}
                selected={Array.isArray(value) ? (value as string[]) : []}
                onChange={(ids) => update(key, ids)}
                description={field.description}
              />
            </div>
          );
        }

        // ---- other array → JSON textarea ----
        if (field.type === "array") {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)} (JSON array)</Label>
              <JsonTextarea
                id={key}
                rows={3}
                canonicalValue={value ?? field.default ?? []}
                onCommit={(v) => update(key, v)}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- object → JSON textarea ----
        if (field.type === "object") {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)} (JSON)</Label>
              <JsonTextarea
                id={key}
                rows={3}
                canonicalValue={value ?? field.default ?? {}}
                onCommit={(v) => update(key, v)}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- boolean → checkbox ----
        if (field.type === "boolean") {
          return (
            <div key={key} className="space-y-1">
              <div className="flex items-center gap-2">
                <input
                  id={key}
                  type="checkbox"
                  checked={Boolean(value ?? field.default ?? false)}
                  onChange={(e) => update(key, e.target.checked)}
                />
                <Label htmlFor={key}>{humanize(key)}</Label>
              </div>
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- number / integer → Input number ----
        if (field.type === "number" || field.type === "integer") {
          const step = field.type === "integer" ? 1 : 0.1;
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <Input
                id={key}
                type="number"
                step={step}
                min={field.min}
                max={field.max}
                value={String(value ?? field.default ?? 0)}
                onChange={(e) =>
                  update(
                    key,
                    field.type === "integer"
                      ? parseInt(e.target.value, 10)
                      : parseFloat(e.target.value),
                  )
                }
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- code field on code_execution → monospace code editor ----
        if (field.type === "string" && key === "code" && nodeType === "code_execution") {
          return (
            <CodeTextarea
              key={key}
              value={String(value ?? field.default ?? "")}
              onChange={(v) => update(key, v)}
              placeholder={"# 'inputs' contains upstream node outputs.\n# Assign your result to 'output'.\n\noutput = {\"result\": inputs}"}
              description={field.description}
            />
          );
        }

        // ---- toolName on mcp_tool → ToolSingleSelect ----
        if (field.type === "string" && key === "toolName" && nodeType === "mcp_tool") {
          return (
            <div key={key} className="space-y-2">
              <Label>{humanize(key)}</Label>
              <ToolSingleSelect
                selected={String(value ?? "")}
                onChange={(name) => update(key, name)}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- string (Jinja2 textarea: systemPrompt) ----
        if (field.type === "string" && JINJA2_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <ExpressionInput
                value={String(value ?? field.default ?? "")}
                onChange={(v) => update(key, v)}
                suggestions={jinja2Suggestions}
                multiline
                rows={4}
                placeholder="Use {{ trigger.field }} or {{ node_2.response }}"
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- string (plain textarea: approvalMessage, body) ----
        if (field.type === "string" && TEXTAREA_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <Textarea
                id={key}
                rows={4}
                value={String(value ?? field.default ?? "")}
                onChange={(e) => update(key, e.target.value)}
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- string (expression field: condition, arrayExpression, *Expression) ----
        if (field.type === "string" && EXPRESSION_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <ExpressionInput
                value={String(value ?? field.default ?? "")}
                onChange={(v) => update(key, v)}
                suggestions={exprSuggestions}
                placeholder="e.g. node_2.intent == &quot;diagnose&quot;"
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- string (node ID reference: responseNodeId, historyNodeId) ----
        if (field.type === "string" && NODE_ID_KEYS.has(key)) {
          return (
            <div key={key} className="space-y-2">
              <Label htmlFor={key}>{humanize(key)}</Label>
              <ExpressionInput
                value={String(value ?? field.default ?? "")}
                onChange={(v) => update(key, v)}
                suggestions={nodeIdSuggestions}
                placeholder="e.g. node_4"
              />
              {field.description && <FieldHint text={field.description} />}
            </div>
          );
        }

        // ---- string → Input text ----
        return (
          <div key={key} className="space-y-2">
            <Label htmlFor={key}>{humanize(key)}</Label>
            <Input
              id={key}
              type="text"
              value={String(value ?? field.default ?? "")}
              onChange={(e) => update(key, e.target.value)}
            />
            {field.description && <FieldHint text={field.description} />}
          </div>
        );
      })}
    </div>
  );
}
