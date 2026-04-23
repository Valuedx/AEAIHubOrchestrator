/**
 * COPILOT-02.i + 02.ii — the chat panel.
 *
 * Layout
 * ------
 *
 * Fixed-width (default 460 px, adjustable via the ``width`` prop so a
 * future resizable-handle feature can drive it from App state) column
 * on the right side of the layout, alongside the canvas. Mutually
 * exclusive with the ``PropertyInspector`` — App.tsx shows one or the
 * other — because the panel needs the horizontal room to keep prose
 * readable. User feedback that shaped this: "panels should be large
 * enough and visible", so we lean generous on width + padding.
 *
 * Three rows::
 *
 *   header      → branding + draft title + Stop/Apply/Close buttons
 *   messages    → scrollable chat history + event stream
 *   composer    → textarea + send
 *
 * Draft + session lifecycle (02.ii adds resume)
 * ---------------------------------------------
 *
 * On each open, bootstrap looks for:
 *
 *   1. a draft we can resume — most-recent active draft matching the
 *      current workflow (or the most-recent net-new draft if no
 *      workflow is loaded). If none, create a fresh one.
 *   2. an active session on that draft. If none, create a fresh
 *      session.
 *   3. the prior turns on the resumed session and replay them into
 *      the message list via ``turnsToChatItems`` so the user sees
 *      their conversation continue exactly where they left off.
 *
 * Streaming + stop
 * ----------------
 *
 * ``api.sendCopilotTurn`` is an async generator that yields
 * ``CopilotAgentEvent`` items. We append each yielded event to
 * ``items`` as it arrives; React re-renders on every state update
 * so the chat reveals event-by-event. On ``done`` we flip
 * ``streaming`` back off.
 *
 * The AbortController on ``abortRef`` is now wired to an explicit
 * "Stop" button in the header (02.ii) in addition to the
 * close-panel/unmount path.
 *
 * Apply / Promote (02.ii)
 * -----------------------
 *
 * The header's "Apply" button is enabled once the draft has at least
 * one mutation (``draft.version > 0`` since fork). It opens a
 * ``PromoteDialog`` that shows diff summary + validation + name
 * field for net-new drafts. On successful promote we reload the
 * workflow store, open the promoted workflow, and close the panel.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleCheck, Loader2, Sparkles, Square, X } from "lucide-react";
import {
  api,
  type CopilotAgentEvent,
  type CopilotDraftOut,
  type CopilotPromoteOut,
  type CopilotSessionOut,
  type CopilotTurnOut,
} from "@/lib/api";
import { useWorkflowStore } from "@/store/workflowStore";
import { useFlowStore } from "@/store/flowStore";
import { CopilotMessageList, type ChatItem } from "./CopilotMessageList";
import { CopilotComposer } from "./CopilotComposer";
import { PromoteDialog } from "./PromoteDialog";


interface Props {
  open: boolean;
  onClose: () => void;
  /** Panel width in pixels. Default 460 — wide enough that tool
   * result cards + prose bubbles don't feel cramped. */
  width?: number;
}


export function CopilotPanel({ open, onClose, width = 460 }: Props) {
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow);
  const fetchWorkflows = useWorkflowStore((s) => s.fetchWorkflows);
  const setCopilotPreview = useFlowStore((s) => s.setCopilotPreview);
  const clearCopilotPreview = useFlowStore((s) => s.clearCopilotPreview);

  const [draft, setDraft] = useState<CopilotDraftOut | null>(null);
  const [session, setSession] = useState<CopilotSessionOut | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [bootstrap, setBootstrap] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  // ----------------------------------------------------------------
  // Draft + session bootstrap — runs each time the panel opens.
  // ----------------------------------------------------------------

  //
  // NOTE: ``bootstrap`` MUST NOT appear in this effect's dependency
  // array. We call ``setBootstrap("loading")`` at the top; if
  // ``bootstrap`` were a dep, that state update would retrigger the
  // effect and fire the previous run's cleanup (``cancelled = true``)
  // before ``await api.listDrafts()`` resolves — the in-flight
  // bootstrap aborts silently and the spinner stays on forever.
  // The ``bootstrappedRef`` guard below prevents double bootstrap
  // within a single open session without depending on the state.
  const bootstrappedRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    if (bootstrappedRef.current) return;

    bootstrappedRef.current = true;
    setBootstrap("loading");
    setBootstrapError(null);
    let cancelled = false;

    (async () => {
      try {
        // Look for the most-recent active draft this user already
        // has open for the current workflow. If we find one, resume
        // it; otherwise create a fresh draft. This is the
        // session-resume path so closing and reopening the panel
        // doesn't lose context.
        const existingDrafts = await api.listDrafts();
        if (cancelled) return;
        const resumableDraft = currentWorkflow?.id
          ? existingDrafts.find((d) => d.base_workflow_id === currentWorkflow.id)
          : existingDrafts.find((d) => d.base_workflow_id === null);

        let loadedDraft: CopilotDraftOut;
        if (resumableDraft) {
          loadedDraft = resumableDraft;
        } else {
          const title = currentWorkflow?.name
            ? `Edits on "${currentWorkflow.name}"`
            : "New workflow draft";
          loadedDraft = await api.createDraft({
            title,
            base_workflow_id: currentWorkflow?.id,
          });
          if (cancelled) return;
        }
        setDraft(loadedDraft);

        // Find the most-recent session for this draft; if none,
        // create a fresh one. Reuse is what makes "close + reopen
        // keeps my conversation" work.
        const existingSessions = await api.listCopilotSessions(loadedDraft.id);
        if (cancelled) return;
        const activeSession = existingSessions.find(
          (s) => s.status === "active",
        );

        let loadedSession: CopilotSessionOut;
        let replayTurns: CopilotTurnOut[] = [];
        if (activeSession) {
          loadedSession = activeSession;
          replayTurns = await api.listCopilotTurns(activeSession.id);
          if (cancelled) return;
        } else {
          loadedSession = await api.createCopilotSession({
            draft_id: loadedDraft.id,
          });
          if (cancelled) return;
        }
        setSession(loadedSession);
        setItems(turnsToChatItems(replayTurns));
        setBootstrap("ready");
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setBootstrapError(msg);
        setBootstrap("error");
        // Reset the ref so the "Retry" button in BootstrapError
        // can re-trigger bootstrap by toggling open or workflow.
        bootstrappedRef.current = false;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, currentWorkflow?.id, currentWorkflow?.name]);

  // Reset the in-memory chat when the panel is closed so a reopen
  // starts fresh. Backend turns are still preserved and readable
  // via listCopilotTurns for a later history-restore feature.
  useEffect(() => {
    if (open) return;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setItems([]);
    setSession(null);
    setDraft(null);
    setStreaming(false);
    setBootstrap("idle");
    // Reset the bootstrap guard so re-opening the panel triggers a
    // fresh bootstrap — otherwise the ref would stay truthy and the
    // useEffect above would skip.
    bootstrappedRef.current = false;
    // COPILOT-02.ii.b — exit the canvas preview so the user returns
    // to editing the base workflow when the panel closes.
    clearCopilotPreview();
  }, [open, clearCopilotPreview]);

  // COPILOT-02.ii.b — drive the canvas preview from draft state.
  // Any time the draft is at a version beyond fork (the copilot
  // has made at least one mutation since it forked) we render the
  // draft graph read-only on the canvas with diff annotations.
  // Below fork = draft is identical to the base workflow, so the
  // live workflow view is what the user wants to see.
  useEffect(() => {
    if (!open) return;
    const baseVersion = draft?.base_version_at_fork ?? 0;
    const hasChanges = (draft?.version ?? 0) > baseVersion;
    if (draft && hasChanges) {
      setCopilotPreview(
        draft.graph_json,
        currentWorkflow?.graph_json ?? null,
      );
    } else {
      clearCopilotPreview();
    }
  }, [
    open,
    draft,
    currentWorkflow,
    setCopilotPreview,
    clearCopilotPreview,
  ]);

  // ----------------------------------------------------------------
  // Send a user turn and stream the response.
  // ----------------------------------------------------------------

  const handleSend = useCallback(
    async (text: string) => {
      if (!session) return;
      const userItem: ChatItem = {
        id: `user-${Date.now()}`,
        kind: "user",
        userText: text,
      };
      setItems((prev) => [...prev, userItem]);
      setStreaming(true);

      const ac = new AbortController();
      abortRef.current = ac;

      let streamError: string | null = null;
      try {
        for await (const event of api.sendCopilotTurn(
          session.id,
          text,
          ac.signal,
        )) {
          setItems((prev) => [
            ...prev,
            {
              id: eventId(event, prev.length),
              kind: "event",
              event,
            },
          ]);
          if (event.type === "tool_result" && event.draft_version !== undefined) {
            // Keep local draft.version in sync so a future resizable
            // graph preview can stay consistent.
            setDraft((d) => (d ? { ...d, version: event.draft_version } : d));
          }
          if (event.type === "done") break;
        }
      } catch (err) {
        if (ac.signal.aborted) {
          // Intentional cancel — don't surface.
        } else {
          streamError = err instanceof Error ? err.message : String(err);
        }
      } finally {
        abortRef.current = null;
        setStreaming(false);
      }

      if (streamError) {
        setItems((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            kind: "event",
            event: {
              type: "error",
              message: streamError,
              recoverable: true,
            },
          },
        ]);
      }
    },
    [session],
  );

  // ----------------------------------------------------------------
  // Stop / promote handlers
  // ----------------------------------------------------------------

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handlePromoted = useCallback(
    async (result: CopilotPromoteOut) => {
      setPromoteOpen(false);
      try {
        await fetchWorkflows();
        await loadWorkflow(result.workflow_id);
      } finally {
        onClose();
      }
    },
    [fetchWorkflows, loadWorkflow, onClose],
  );

  // Apply button becomes clickable once the draft has at least one
  // mutation since fork. Net-new drafts start at 0 → 1 on first
  // tool call; forks start at their base's version (captured as
  // ``base_version_at_fork``) and bump from there, so we compare
  // against the fork point rather than a naive >0.
  const baseVersionAtFork = draft?.base_version_at_fork ?? 0;
  const hasDraftChanges = (draft?.version ?? 0) > baseVersionAtFork;

  const baseNodeCount = currentWorkflow?.graph_json?.nodes?.length;
  const baseEdgeCount = currentWorkflow?.graph_json?.edges?.length;

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------

  if (!open) return null;

  const panelStyle = { width: `${width}px` };

  return (
    <div
      style={panelStyle}
      className="flex flex-col h-full border-l bg-sidebar shrink-0"
      aria-label="Workflow authoring copilot"
    >
      <PanelHeader
        draftTitle={draft?.title}
        onClose={onClose}
        onApply={draft && hasDraftChanges ? () => setPromoteOpen(true) : undefined}
        onStop={streaming ? handleStop : undefined}
      />

      {bootstrap === "loading" && <BootstrapLoading />}

      {bootstrap === "error" && (
        <BootstrapError
          message={bootstrapError ?? "Could not start session"}
          onRetry={() => {
            // Reset both the ref AND the state so the bootstrap
            // useEffect picks up a clean idle transition on the
            // next currentWorkflow?.id toggle — or the user can
            // close+reopen which definitely re-fires.
            bootstrappedRef.current = false;
            setBootstrap("idle");
          }}
        />
      )}

      {bootstrap === "ready" && (
        <>
          <CopilotMessageList items={items} streaming={streaming} />
          <CopilotComposer onSend={handleSend} streaming={streaming} />
        </>
      )}

      <PromoteDialog
        open={promoteOpen}
        onClose={() => setPromoteOpen(false)}
        draft={draft}
        baseWorkflowName={currentWorkflow?.name ?? null}
        baseNodeCount={baseNodeCount}
        baseEdgeCount={baseEdgeCount}
        onPromoted={handlePromoted}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// Panel header
// ---------------------------------------------------------------------------


function PanelHeader({
  draftTitle,
  onClose,
  onApply,
  onStop,
}: {
  draftTitle: string | undefined;
  onClose: () => void;
  /** When set, renders an "Apply" button that opens PromoteDialog.
   * Undefined = no pending changes, button hidden. */
  onApply?: () => void;
  /** When set (i.e. a turn is streaming), renders a "Stop" button
   * that aborts the in-flight request. */
  onStop?: () => void;
}) {
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b">
      <Sparkles className="h-4 w-4 text-primary shrink-0" />
      <div className="flex-1 min-w-0">
        <h2 className="text-sm font-semibold leading-tight">Workflow copilot</h2>
        {draftTitle && (
          <p
            className="text-[11px] text-muted-foreground truncate"
            title={draftTitle}
          >
            Draft · {draftTitle}
          </p>
        )}
      </div>
      {onStop && (
        <button
          type="button"
          onClick={onStop}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-destructive/40 text-destructive text-[11px] hover:bg-destructive/10 transition-colors"
          aria-label="Stop generating"
          title="Stop generating"
        >
          <Square className="h-3 w-3" />
          Stop
        </button>
      )}
      {onApply && (
        <button
          type="button"
          onClick={onApply}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary text-primary-foreground text-[11px] hover:opacity-90 transition-opacity"
          aria-label="Apply draft"
          title="Review and promote draft"
        >
          <CircleCheck className="h-3 w-3" />
          Apply
        </button>
      )}
      <button
        type="button"
        onClick={onClose}
        className="p-1 rounded-md hover:bg-accent transition-colors"
        aria-label="Close copilot"
        title="Close copilot"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}


function BootstrapLoading() {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center text-muted-foreground gap-2">
      <Loader2 className="h-5 w-5 animate-spin" />
      <p className="text-sm">Starting a draft session…</p>
    </div>
  );
}


function BootstrapError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center p-6 text-center text-sm">
      <p className="text-destructive font-medium">Could not start the copilot</p>
      <p className="text-muted-foreground mt-1 break-words">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 px-3 py-1.5 rounded-md border text-xs hover:bg-accent"
      >
        Retry
      </button>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


/**
 * Stable-ish key for each event in the chat list. Tool calls + results
 * come with their own id; text and done events don't, so we fall back
 * to the current chat length.
 */
function eventId(event: CopilotAgentEvent, fallbackIndex: number): string {
  if (event.type === "tool_call" || event.type === "tool_result") {
    return `${event.type}-${event.id}`;
  }
  return `${event.type}-${fallbackIndex}-${Date.now()}`;
}


/**
 * Replay prior ``copilot_turns`` rows as ``ChatItem`` entries so the
 * panel reopens with the conversation already on screen. The backend
 * stores three shapes (see ``_persist_turn`` in agent.py):
 *
 *   user      → ``{text}``
 *   assistant → ``{text, blocks: [{type:"text"} | {type:"tool_use"}]}``
 *                plus normalised ``tool_calls_json`` list
 *   tool      → ``{tool_use_id, name, args, result, error?}``
 *
 * We fan each turn out into one or more ``ChatItem`` entries so the
 * live-stream rendering path can be reused verbatim — user text lands
 * as a right-aligned bubble and every assistant/tool artifact becomes
 * a dispatched ``CopilotEventCard``. Tool turns have no per-turn
 * validation or draft_version snapshot persisted, so the replayed
 * ``tool_result`` event uses ``validation: null`` and ``draft_version: 0``
 * — live streams continue to carry the real values.
 */
export function turnsToChatItems(turns: CopilotTurnOut[]): ChatItem[] {
  const items: ChatItem[] = [];
  for (const turn of turns) {
    const content = (turn.content_json ?? {}) as Record<string, unknown>;
    if (turn.role === "user") {
      items.push({
        id: `replay-user-${turn.id}`,
        kind: "user",
        userText: typeof content.text === "string" ? content.text : "",
      });
    } else if (turn.role === "assistant") {
      const text = typeof content.text === "string" ? content.text : "";
      if (text) {
        items.push({
          id: `replay-text-${turn.id}`,
          kind: "event",
          event: { type: "assistant_text", text },
        });
      }
      const toolCalls = Array.isArray(turn.tool_calls_json)
        ? (turn.tool_calls_json as Array<Record<string, unknown>>)
        : [];
      for (const tc of toolCalls) {
        const id = typeof tc.id === "string" ? tc.id : `tc-${turn.id}`;
        const name = typeof tc.name === "string" ? tc.name : "";
        const input = (tc.input ?? tc.args ?? {}) as Record<string, unknown>;
        items.push({
          id: `replay-toolcall-${id}`,
          kind: "event",
          event: { type: "tool_call", id, name, args: input },
        });
      }
    } else if (turn.role === "tool") {
      const id =
        typeof content.tool_use_id === "string"
          ? content.tool_use_id
          : `tr-${turn.id}`;
      const name = typeof content.name === "string" ? content.name : "";
      const result = (content.result ?? {}) as Record<string, unknown>;
      const error = typeof content.error === "string" ? content.error : null;
      items.push({
        id: `replay-toolresult-${id}`,
        kind: "event",
        event: {
          type: "tool_result",
          id,
          name,
          result,
          validation: null,
          draft_version: 0,
          error,
        },
      });
    }
  }
  return items;
}
