/**
 * API-18A — API Playground dialog.
 *
 * In-app test console for the currently loaded workflow. Sends trigger
 * payloads to ``POST /execute`` (sync or async) without leaving the UI
 * — eliminates the reach-for-curl detour most visual workflow tools
 * don't ship yet (Dify has one, Flowise has one, we didn't).
 *
 * Scope boundaries (v1):
 *
 *   * No embedded SSE log tail. For async runs we show the returned
 *     ``InstanceOut`` and nudge the operator toward the main Execution
 *     Panel. Duplicating ExecutionPanel's streaming UI inside a modal
 *     would be bigger than the DX win.
 *   * No per-workflow schema inference. The payload editor is a plain
 *     JSON textarea; parse errors show inline. We can teach it to
 *     read a Webhook Trigger's ``bodySchema`` later if operators ask.
 *   * No auth-mode awareness beyond what buildCurl does — the actual
 *     ``executeWorkflow`` call goes through ``api.ts`` which already
 *     attaches the right header / bearer.
 *
 * Related features:
 *   * DV-02 (``/nodes/{id}/test``) is the single-node probe — orthogonal
 *     to this whole-workflow test console.
 *   * Future API-18B (chatbot embed widget) will expose a public-facing
 *     chat UI; this dialog stays in-app and requires the operator to
 *     be signed in.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  Copy,
  FlaskConical,
  Loader2,
  Trash2,
  Upload,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { api, type InstanceOut, type SyncExecuteOut, type WorkflowOut } from "@/lib/api";
import { buildCurl } from "@/lib/playgroundCurl";
import {
  addToHistory,
  clearHistory,
  loadHistory,
  type PlaygroundHistoryEntry,
} from "@/lib/playgroundHistory";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflow: WorkflowOut | null;
}

// Terminal statuses that the sync/async shapes surface on completion.
const TERMINAL_OK = new Set(["completed"]);
const TERMINAL_BAD = new Set(["failed", "cancelled", "timed_out"]);

type RunResult =
  | { kind: "sync"; out: SyncExecuteOut; elapsed_ms: number }
  | { kind: "async"; out: InstanceOut }
  | { kind: "error"; message: string };


export function ApiPlaygroundDialog({ open, onOpenChange, workflow }: Props) {
  const [payloadText, setPayloadText] = useState<string>("{}");
  const [sync, setSync] = useState<boolean>(true);
  const [syncTimeout, setSyncTimeout] = useState<number>(120);
  const [deterministic, setDeterministic] = useState<boolean>(false);
  const [running, setRunning] = useState<boolean>(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [history, setHistory] = useState<PlaygroundHistoryEntry[]>([]);
  const [curlCopied, setCurlCopied] = useState<boolean>(false);
  const [parseError, setParseError] = useState<string | null>(null);

  // Re-sync from localStorage when the dialog opens on a different workflow.
  useEffect(() => {
    if (!open || !workflow) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setHistory(loadHistory(workflow.id));
      setResult(null);
      setCurlCopied(false);
      setParseError(null);
    });
    return () => {
      cancelled = true;
    };
  }, [open, workflow]);

  const curlText = useMemo(() => {
    if (!workflow) return "";
    // Parse with a lenient try so the curl updates live as the user
    // types — an invalid body just round-trips as the raw string.
    let parsed: unknown = null;
    const text = payloadText.trim();
    if (text !== "") {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text; // Let the user see the literal in the curl.
      }
    }
    return buildCurl({
      workflowId: workflow.id,
      payload: parsed,
      sync,
      syncTimeout,
      deterministicMode: deterministic,
      baseUrl: import.meta.env.VITE_API_URL as string | undefined,
      tenantId: import.meta.env.VITE_TENANT_ID as string | undefined,
      authMode: import.meta.env.VITE_AUTH_MODE as string | undefined,
    });
  }, [workflow, payloadText, sync, syncTimeout, deterministic]);

  const handleRun = useCallback(async () => {
    if (!workflow) return;
    setParseError(null);

    // Accept "" or whitespace-only as {}; otherwise parse strictly.
    let payload: Record<string, unknown> | undefined = undefined;
    const trimmed = payloadText.trim();
    if (trimmed !== "") {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed === null) {
          payload = undefined;
        } else if (typeof parsed !== "object" || Array.isArray(parsed)) {
          setParseError("Payload must be a JSON object (not an array or primitive).");
          return;
        } else {
          payload = parsed as Record<string, unknown>;
        }
      } catch (e) {
        setParseError(`Invalid JSON: ${(e as Error).message}`);
        return;
      }
    }

    setRunning(true);
    setResult(null);
    const start = performance.now();
    try {
      const out = await api.executeWorkflow(
        workflow.id,
        payload,
        deterministic,
        sync,
        sync ? syncTimeout : undefined,
      );
      const elapsed = Math.round(performance.now() - start);

      if (sync) {
        const syncOut = out as SyncExecuteOut;
        setResult({ kind: "sync", out: syncOut, elapsed_ms: elapsed });
        setHistory(
          addToHistory(workflow.id, {
            at: Date.now(),
            mode: "sync",
            status: syncOut.status,
            elapsed_ms: elapsed,
            payload: payloadText,
            instance_id: syncOut.instance_id,
          }),
        );
      } else {
        const asyncOut = out as InstanceOut;
        setResult({ kind: "async", out: asyncOut });
        setHistory(
          addToHistory(workflow.id, {
            at: Date.now(),
            mode: "async",
            status: asyncOut.status || "queued",
            elapsed_ms: null,
            payload: payloadText,
            instance_id: asyncOut.id,
          }),
        );
      }
    } catch (e) {
      const message = String((e as Error)?.message || e);
      setResult({ kind: "error", message });
      setHistory(
        addToHistory(workflow.id, {
          at: Date.now(),
          mode: sync ? "sync" : "async",
          status: "error",
          elapsed_ms: null,
          payload: payloadText,
          instance_id: null,
        }),
      );
    } finally {
      setRunning(false);
    }
  }, [workflow, payloadText, sync, syncTimeout, deterministic]);

  const handleCopyCurl = useCallback(() => {
    if (!curlText) return;
    navigator.clipboard.writeText(curlText).then(() => {
      setCurlCopied(true);
      window.setTimeout(() => setCurlCopied(false), 2000);
    });
  }, [curlText]);

  const handleLoadPayload = useCallback((entry: PlaygroundHistoryEntry) => {
    setPayloadText(entry.payload);
    setParseError(null);
  }, []);

  const handleClearHistory = useCallback(() => {
    if (!workflow) return;
    if (!confirm("Clear the run history for this workflow?")) return;
    clearHistory(workflow.id);
    setHistory([]);
  }, [workflow]);

  if (!workflow) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4" />
            API Playground
            <span className="text-xs text-muted-foreground font-normal truncate">
              — {workflow.name}
            </span>
          </DialogTitle>
        </DialogHeader>
        <Separator />

        <ScrollArea className="flex-1 min-h-0">
          <div className="p-1 space-y-4">
            {/* Payload editor */}
            <section className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="playground-payload">Trigger payload (JSON)</Label>
                {parseError && (
                  <span className="text-[11px] text-destructive">{parseError}</span>
                )}
              </div>
              <textarea
                id="playground-payload"
                value={payloadText}
                onChange={(e) => setPayloadText(e.target.value)}
                rows={8}
                spellCheck={false}
                placeholder='{"message": "hello"}'
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs font-mono resize-y"
              />
              <p className="text-[11px] text-muted-foreground">
                Sent as the workflow's <code className="text-[10px] bg-muted px-1 py-0.5 rounded">trigger</code> payload.
                Leave empty to send <code className="text-[10px] bg-muted px-1 py-0.5 rounded">null</code>.
              </p>
            </section>

            {/* Controls row */}
            <section className="flex flex-wrap items-center gap-3 text-sm">
              <label className="flex items-center gap-1.5 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={sync}
                  onChange={(e) => setSync(e.target.checked)}
                  className="rounded border-input"
                />
                Sync
              </label>
              {sync && (
                <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  timeout (s):
                  <input
                    type="number"
                    min={5}
                    max={3600}
                    value={syncTimeout}
                    onChange={(e) => setSyncTimeout(Math.max(5, Math.min(3600, Number(e.target.value) || 120)))}
                    className="w-16 h-7 rounded border border-input bg-background px-1.5 text-xs"
                  />
                </label>
              )}
              <label className="flex items-center gap-1.5 cursor-pointer select-none text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={deterministic}
                  onChange={(e) => setDeterministic(e.target.checked)}
                  className="rounded border-input"
                />
                Deterministic mode
              </label>
              <div className="flex-1" />
              <Button
                size="sm"
                onClick={handleRun}
                disabled={running}
                className="gap-1.5"
              >
                {running ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Upload className="h-3.5 w-3.5" />
                )}
                Run
              </Button>
            </section>

            {/* Result pane */}
            {result && <ResultPane result={result} />}

            {/* Copy-as-curl */}
            <section className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>Equivalent curl</Label>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleCopyCurl}
                  className="h-7 gap-1.5 text-[11px]"
                >
                  {curlCopied ? (
                    <>
                      <Check className="h-3 w-3 text-green-500" /> Copied
                    </>
                  ) : (
                    <>
                      <Copy className="h-3 w-3" /> Copy
                    </>
                  )}
                </Button>
              </div>
              <pre className="text-[11px] font-mono bg-muted/40 rounded-md p-2 whitespace-pre-wrap break-all leading-snug">
                {curlText}
              </pre>
            </section>

            {/* History */}
            <section className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label>Recent runs <span className="text-[11px] text-muted-foreground">({history.length})</span></Label>
                {history.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleClearHistory}
                    className="h-7 gap-1.5 text-[11px] text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3" /> Clear
                  </Button>
                )}
              </div>
              {history.length === 0 ? (
                <p className="text-[11px] text-muted-foreground italic">
                  No runs yet for this workflow. Press Run above.
                </p>
              ) : (
                <ul className="space-y-1">
                  {history.map((entry) => (
                    <li
                      key={entry.at}
                      className="flex items-center gap-2 text-[11px] rounded border px-2 py-1.5 hover:bg-accent/40"
                    >
                      <StatusChip status={entry.status} />
                      <span className="text-muted-foreground shrink-0">{entry.mode}</span>
                      {entry.elapsed_ms != null && (
                        <span className="text-muted-foreground shrink-0">
                          {entry.elapsed_ms} ms
                        </span>
                      )}
                      <span className="text-muted-foreground truncate font-mono">
                        {truncate(entry.payload, 60)}
                      </span>
                      <div className="flex-1" />
                      <span className="text-muted-foreground shrink-0">
                        {new Date(entry.at).toLocaleTimeString()}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleLoadPayload(entry)}
                        className="h-6 px-2 text-[10px]"
                      >
                        Load
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}


// ---------------------------------------------------------------------------
// Result pane — compact display of the last run outcome.
// ---------------------------------------------------------------------------


function ResultPane({ result }: { result: RunResult }) {
  if (result.kind === "error") {
    return (
      <section className="rounded-md border border-destructive/50 bg-destructive/5 p-2.5 text-xs space-y-1">
        <div className="flex items-center gap-2 font-medium text-destructive">
          Request failed
        </div>
        <pre className="text-[11px] font-mono whitespace-pre-wrap break-all">
          {result.message}
        </pre>
      </section>
    );
  }

  if (result.kind === "sync") {
    return (
      <section className="rounded-md border p-2.5 space-y-1.5">
        <div className="flex items-center gap-2 text-sm">
          <StatusChip status={result.out.status} />
          <span className="text-muted-foreground text-[11px]">
            instance {result.out.instance_id.slice(0, 8)}…
          </span>
          <div className="flex-1" />
          <span className="text-[11px] text-muted-foreground">
            {result.elapsed_ms} ms (client)
          </span>
        </div>
        <div>
          <Label className="text-[11px]">Output context</Label>
          <pre className="mt-1 text-[11px] font-mono bg-muted/40 rounded-md p-2 whitespace-pre-wrap break-all max-h-64 overflow-auto">
            {JSON.stringify(result.out.output, null, 2)}
          </pre>
        </div>
      </section>
    );
  }

  // async
  return (
    <section className="rounded-md border p-2.5 space-y-1.5 text-sm">
      <div className="flex items-center gap-2">
        <StatusChip status={result.out.status || "queued"} />
        <span className="text-muted-foreground text-[11px]">
          instance {result.out.id.slice(0, 8)}…
        </span>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Async run queued. Close this dialog and use the Execution Panel to stream logs.
      </p>
    </section>
  );
}


function StatusChip({ status }: { status: string }) {
  const tone =
    TERMINAL_OK.has(status)
      ? "bg-green-500/10 text-green-700 border-green-500/30"
      : TERMINAL_BAD.has(status) || status === "error"
        ? "bg-red-500/10 text-red-700 border-red-500/30"
        : "bg-muted text-muted-foreground border-muted-foreground/20";
  return (
    <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${tone}`}>
      {status}
    </Badge>
  );
}


function truncate(text: string, n: number): string {
  const flat = text.replace(/\s+/g, " ");
  return flat.length > n ? flat.slice(0, n - 1) + "…" : flat;
}
