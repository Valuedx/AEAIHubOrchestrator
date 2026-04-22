/**
 * COPILOT-02.i — renders one agent event as a message bubble.
 *
 * Every SSE event from the backend has a ``type`` discriminator
 * (see ``CopilotAgentEvent`` in ``lib/api.ts``). This component
 * dispatches on it and renders a family-specific card:
 *
 *   - ``assistant_text``  → prose bubble on the left
 *   - ``tool_call``       → neutral pill with tool name + args summary
 *   - ``tool_result``     → colour-coded card (green ok / red error)
 *                           with node ids, validation, extra details
 *                           expandable
 *   - ``error``           → red banner (recoverable vs. not)
 *   - ``done``            → hidden (just a stream terminator)
 *
 * User turns are rendered by the list component directly — they come
 * from the chat history, not the event stream. Assistant text bubbles
 * use a wider max-width than tool cards so paragraphs stay readable
 * without feeling cramped (user feedback: "panels should be large
 * enough and visible").
 */

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleX,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import type {
  CopilotAgentEvent,
  CopilotDraftValidation,
  CopilotLint,
} from "@/lib/api";

interface Props {
  event: CopilotAgentEvent;
}

export function CopilotEventCard({ event }: Props) {
  if (event.type === "done") return null;

  if (event.type === "assistant_text") {
    return (
      <div className="flex flex-col gap-1 items-start">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Copilot
        </span>
        <div className="rounded-lg rounded-tl-sm bg-muted/60 px-3.5 py-2.5 text-sm leading-relaxed max-w-[95%] whitespace-pre-wrap break-words">
          {event.text || <em className="text-muted-foreground">(empty response)</em>}
        </div>
      </div>
    );
  }

  if (event.type === "error") {
    return (
      <div
        role="alert"
        className={`rounded-md border px-3 py-2 text-[12px] ${
          event.recoverable
            ? "border-amber-500/40 bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-100"
            : "border-destructive/40 bg-destructive/10 text-destructive"
        }`}
      >
        <div className="flex items-start gap-2">
          <TriangleAlert className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium">
              {event.recoverable ? "Recoverable error" : "Agent error"}
            </p>
            <p className="mt-0.5 opacity-90 break-words">{event.message}</p>
          </div>
        </div>
      </div>
    );
  }

  if (event.type === "tool_call") {
    return (
      <ToolCallPill name={event.name} args={event.args} />
    );
  }

  if (event.type === "tool_result") {
    return <ToolResultCard event={event} />;
  }

  // Exhaustive check — if a new event type is added in the backend,
  // TypeScript's discriminated union flags the missing branch here.
  const _exhaustive: never = event;
  return _exhaustive;
}


// ---------------------------------------------------------------------------
// Tool call — in-flight pill shown BEFORE the tool_result arrives.
// Keeps the chat honest about what the agent is doing so the user
// isn't staring at a blank panel mid-latency.
// ---------------------------------------------------------------------------


function ToolCallPill({
  name,
  args,
}: {
  name: string;
  args: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  const summary = summariseArgs(name, args);

  return (
    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <Wrench className="h-3 w-3 shrink-0" />
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-muted/40 px-2 py-0.5 hover:bg-muted transition-colors"
      >
        {open ? (
          <ChevronDown className="h-2.5 w-2.5" />
        ) : (
          <ChevronRight className="h-2.5 w-2.5" />
        )}
        <span className="font-mono">{name}</span>
        {summary && <span className="opacity-70">— {summary}</span>}
      </button>
      {open && (
        <pre className="mt-1 w-full max-w-full overflow-x-auto rounded bg-muted/40 p-2 text-[10px] font-mono">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}
    </div>
  );
}


// Short one-liner for common tool calls; falls through to JSON.
function summariseArgs(name: string, args: Record<string, unknown>): string {
  if (!args || typeof args !== "object") return "";
  switch (name) {
    case "add_node":
      return String(args.node_type ?? "");
    case "update_node_config":
    case "delete_node":
      return String(args.node_id ?? "");
    case "connect_nodes":
      return `${args.source ?? "?"} → ${args.target ?? "?"}`;
    case "disconnect_edge":
      return String(args.edge_id ?? "");
    case "get_node_schema":
    case "get_node_examples":
      return String(args.node_type ?? args.type ?? "");
    case "list_node_types":
      return args.category ? `category=${args.category}` : "";
    case "search_docs":
      return args.query ? `"${String(args.query).slice(0, 40)}"` : "";
    case "test_node":
      return String(args.node_id ?? "");
    case "execute_draft":
      return "whole graph";
    case "get_execution_logs":
      return args.node_id
        ? `instance=${short(String(args.instance_id ?? ""))} node=${args.node_id}`
        : `instance=${short(String(args.instance_id ?? ""))}`;
    case "get_automationedge_handoff_info":
      return "AE fork";
    case "discover_mcp_tools":
      return args.server_label ? `server=${args.server_label}` : "default server";
    default:
      return "";
  }
}


function short(id: string): string {
  return id.split("-")[0] || id;
}


// ---------------------------------------------------------------------------
// Tool result — card. Expands to show the full result JSON + any
// validation payload on mutation tools.
// ---------------------------------------------------------------------------


function ToolResultCard({
  event,
}: {
  event: Extract<CopilotAgentEvent, { type: "tool_result" }>;
}) {
  const [open, setOpen] = useState(false);
  const isError = !!event.error;

  return (
    <div
      className={`rounded-md border text-[12px] ${
        isError
          ? "border-destructive/40 bg-destructive/5"
          : "border-border/60 bg-muted/30"
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`${open ? "Collapse" : "Expand"} tool result for ${event.name}`}
        className="w-full flex items-start gap-2 px-3 py-2 text-left"
      >
        {isError ? (
          <CircleX className="h-3.5 w-3.5 mt-0.5 shrink-0 text-destructive" />
        ) : (
          <CircleCheck className="h-3.5 w-3.5 mt-0.5 shrink-0 text-emerald-500" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono font-medium">{event.name}</span>
            {isError ? (
              <span className="text-destructive">failed</span>
            ) : (
              <ResultSummary event={event} />
            )}
          </div>
          {isError && (
            <p className="mt-0.5 text-destructive/90 break-words">{event.error}</p>
          )}
          {event.validation && event.validation.warnings.length > 0 && (
            <p className="mt-0.5 text-amber-700 dark:text-amber-300">
              {event.validation.warnings.length} validation warning
              {event.validation.warnings.length === 1 ? "" : "s"}
            </p>
          )}
          {event.validation?.lints && event.validation.lints.length > 0 && (
            <p className="mt-0.5">
              {(() => {
                const errs = event.validation.lints.filter((l) => l.severity === "error").length;
                const warns = event.validation.lints.filter((l) => l.severity === "warn").length;
                return (
                  <>
                    {errs > 0 && (
                      <span className="text-destructive mr-2">
                        {errs} lint error{errs === 1 ? "" : "s"}
                      </span>
                    )}
                    {warns > 0 && (
                      <span className="text-amber-700 dark:text-amber-300">
                        {warns} lint warning{warns === 1 ? "" : "s"}
                      </span>
                    )}
                  </>
                );
              })()}
            </p>
          )}
        </div>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
        )}
      </button>
      {open && (
        <div className="border-t border-border/60 px-3 py-2 space-y-2">
          <DetailSection label="Result">
            <pre className="rounded bg-muted/50 p-2 text-[10px] font-mono whitespace-pre-wrap break-words">
              {JSON.stringify(event.result ?? {}, null, 2)}
            </pre>
          </DetailSection>
          {event.validation && (
            <ValidationSection v={event.validation} />
          )}
          {event.draft_version !== undefined && (
            <p className="text-[10px] text-muted-foreground">
              draft v{event.draft_version}
            </p>
          )}
        </div>
      )}
    </div>
  );
}


function ResultSummary({
  event,
}: {
  event: Extract<CopilotAgentEvent, { type: "tool_result" }>;
}) {
  const r = (event.result ?? {}) as Record<string, unknown>;
  switch (event.name) {
    case "add_node":
      return <span className="text-emerald-700 dark:text-emerald-400">added {String(r.node_id ?? "")}</span>;
    case "update_node_config":
    case "delete_node":
      return <span className="text-emerald-700 dark:text-emerald-400">{String(r.node_id ?? "")}</span>;
    case "connect_nodes":
      return <span className="text-emerald-700 dark:text-emerald-400">{String(r.edge_id ?? "")}</span>;
    case "disconnect_edge":
      return <span className="text-emerald-700 dark:text-emerald-400">removed</span>;
    case "validate_graph": {
      const errs = Array.isArray(r.errors) ? r.errors.length : 0;
      const warns = Array.isArray(r.warnings) ? r.warnings.length : 0;
      return (
        <span className={errs ? "text-destructive" : warns ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-400"}>
          {errs} errors · {warns} warnings
        </span>
      );
    }
    case "list_node_types": {
      const types = Array.isArray(r.node_types) ? r.node_types.length : 0;
      return <span className="text-muted-foreground">{types} types</span>;
    }
    case "execute_draft": {
      const status = String(r.status ?? "");
      const ms = typeof r.elapsed_ms === "number" ? r.elapsed_ms : 0;
      return <span className={status === "completed" ? "text-emerald-700 dark:text-emerald-400" : status === "timeout" ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}>
        {status} {ms ? `(${ms} ms)` : ""}
      </span>;
    }
    case "get_execution_logs": {
      const count = typeof r.log_count === "number" ? r.log_count : 0;
      return <span className="text-muted-foreground">{count} log{count === 1 ? "" : "s"}</span>;
    }
    case "search_docs": {
      const count = typeof r.match_count === "number" ? r.match_count : 0;
      return <span className="text-muted-foreground">{count} match{count === 1 ? "" : "es"}</span>;
    }
    case "test_node": {
      const err = typeof r.error === "string" ? r.error : null;
      const ms = typeof r.elapsed_ms === "number" ? r.elapsed_ms : 0;
      return err ? (
        <span className="text-destructive">error ({ms} ms)</span>
      ) : (
        <span className="text-emerald-700 dark:text-emerald-400">ok ({ms} ms)</span>
      );
    }
    case "get_automationedge_handoff_info": {
      const conns = Array.isArray(r.existing_connections) ? r.existing_connections.length : 0;
      return <span className="text-muted-foreground">{conns} connection{conns === 1 ? "" : "s"}</span>;
    }
    case "discover_mcp_tools": {
      if (r.discovery_enabled === false) {
        return <span className="text-muted-foreground italic">disabled</span>;
      }
      const tools = Array.isArray(r.tools) ? r.tools.length : 0;
      return <span className="text-muted-foreground">{tools} MCP tool{tools === 1 ? "" : "s"}</span>;
    }
    default:
      return <span className="text-muted-foreground">ok</span>;
  }
}


function DetailSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground mb-0.5">
        {label}
      </p>
      {children}
    </div>
  );
}


function ValidationSection({ v }: { v: CopilotDraftValidation }) {
  const hasSchemaIssues = v.errors.length > 0 || v.warnings.length > 0;
  const hasLints = (v.lints?.length ?? 0) > 0;
  if (!hasSchemaIssues && !hasLints) {
    return (
      <p className="text-[11px] text-muted-foreground">
        Validation: clean
        {v.lints_enabled === false && (
          <span className="ml-1 italic">
            (lints disabled per tenant policy)
          </span>
        )}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {hasSchemaIssues && (
        <DetailSection label="Validation">
          <ul className="space-y-0.5 text-[11px]">
            {v.errors.map((e, i) => (
              <li key={`err-${i}`} className="text-destructive">• {e}</li>
            ))}
            {v.warnings.map((w, i) => (
              <li
                key={`warn-${i}`}
                className="text-amber-700 dark:text-amber-300"
              >
                • {w}
              </li>
            ))}
          </ul>
        </DetailSection>
      )}
      {hasLints && (
        <DetailSection label="Lints (SMART-04)">
          <ul className="space-y-1.5 text-[11px]">
            {v.lints!.map((l, i) => (
              <LintCard key={`lint-${i}`} lint={l} />
            ))}
          </ul>
        </DetailSection>
      )}
    </div>
  );
}


function LintCard({ lint }: { lint: CopilotLint }) {
  const isError = lint.severity === "error";
  return (
    <li
      className={`rounded border px-2 py-1.5 ${
        isError
          ? "border-destructive/40 bg-destructive/5"
          : "border-amber-500/30 bg-amber-50 dark:bg-amber-950/20"
      }`}
    >
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          className={`text-[9px] font-mono uppercase px-1 py-0.5 rounded ${
            isError
              ? "bg-destructive/20 text-destructive"
              : "bg-amber-500/20 text-amber-900 dark:text-amber-100"
          }`}
        >
          {lint.severity}
        </span>
        <span className="font-mono text-[10px] opacity-80">{lint.code}</span>
        {lint.node_id && (
          <span className="font-mono text-[10px] text-muted-foreground">
            · {lint.node_id}
          </span>
        )}
      </div>
      <p className="mt-0.5">{lint.message}</p>
      {lint.fix_hint && (
        <p className="mt-0.5 text-muted-foreground">
          <span className="font-medium">Fix:</span> {lint.fix_hint}
        </p>
      )}
    </li>
  );
}
