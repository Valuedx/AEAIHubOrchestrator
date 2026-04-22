/**
 * COPILOT-02.i — the chat panel.
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
 *   header      → copilot branding + draft title + close button
 *   messages    → scrollable chat history + event stream
 *   composer    → textarea + send
 *
 * Draft + session lifecycle
 * -------------------------
 *
 * On mount (every time the panel opens) we ensure there's a draft
 * and a session bound to it:
 *
 *   - If the user has a currently-loaded workflow, create a draft
 *     with ``base_workflow_id`` set so a promote path will land as
 *     a new version of that workflow.
 *   - Otherwise create a net-new blank draft; promote will ask for
 *     a workflow name.
 *
 * The draft + session are created on the backend via the existing
 * ``/api/v1/copilot/drafts`` and ``/api/v1/copilot/sessions`` APIs
 * (01a + 01b.i). When the user closes the panel and reopens it,
 * this component currently creates a fresh session against the
 * same draft — chat history from prior sessions on the same draft
 * is still queryable via ``listCopilotTurns`` but not replayed
 * into the UI yet (kept for 02.ii).
 *
 * Streaming
 * ---------
 *
 * ``api.sendCopilotTurn`` is an async generator that yields
 * ``CopilotAgentEvent`` items. We append each yielded event to
 * ``items`` as it arrives; React re-renders on every state update
 * so the chat reveals token-free but event-by-event. On ``done`` we
 * flip ``streaming`` back off.
 *
 * AbortController is wired up so navigation away from the panel (or
 * a rapid close/reopen) cancels the in-flight fetch. Follow-up
 * (02.ii) adds an explicit "stop generating" button.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import { api, type CopilotAgentEvent, type CopilotDraftOut, type CopilotSessionOut } from "@/lib/api";
import { useWorkflowStore } from "@/store/workflowStore";
import { CopilotMessageList, type ChatItem } from "./CopilotMessageList";
import { CopilotComposer } from "./CopilotComposer";


interface Props {
  open: boolean;
  onClose: () => void;
  /** Panel width in pixels. Default 460 — wide enough that tool
   * result cards + prose bubbles don't feel cramped. */
  width?: number;
}


export function CopilotPanel({ open, onClose, width = 460 }: Props) {
  const currentWorkflow = useWorkflowStore((s) => s.currentWorkflow);
  const [draft, setDraft] = useState<CopilotDraftOut | null>(null);
  const [session, setSession] = useState<CopilotSessionOut | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [bootstrap, setBootstrap] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // ----------------------------------------------------------------
  // Draft + session bootstrap — runs each time the panel opens.
  // ----------------------------------------------------------------

  useEffect(() => {
    if (!open) return;
    if (bootstrap === "ready" || bootstrap === "loading") return;

    setBootstrap("loading");
    setBootstrapError(null);
    let cancelled = false;

    (async () => {
      try {
        // Build a sensible default draft title. If the user has a
        // workflow open, fork it; otherwise "Untitled draft".
        const title = currentWorkflow?.name
          ? `Edits on "${currentWorkflow.name}"`
          : "New workflow draft";
        const created = await api.createDraft({
          title,
          base_workflow_id: currentWorkflow?.id,
        });
        if (cancelled) return;
        setDraft(created);

        // Start a chat session against the draft. Provider defaults
        // to anthropic server-side; 02.ii will surface a picker.
        const newSession = await api.createCopilotSession({
          draft_id: created.id,
        });
        if (cancelled) return;
        setSession(newSession);
        setBootstrap("ready");
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setBootstrapError(msg);
        setBootstrap("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, bootstrap, currentWorkflow?.id, currentWorkflow?.name]);

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
  }, [open]);

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
      <PanelHeader draftTitle={draft?.title} onClose={onClose} />

      {bootstrap === "loading" && <BootstrapLoading />}

      {bootstrap === "error" && (
        <BootstrapError
          message={bootstrapError ?? "Could not start session"}
          onRetry={() => setBootstrap("idle")}
        />
      )}

      {bootstrap === "ready" && (
        <>
          <CopilotMessageList items={items} streaming={streaming} />
          <CopilotComposer onSend={handleSend} streaming={streaming} />
        </>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Panel header
// ---------------------------------------------------------------------------


function PanelHeader({
  draftTitle,
  onClose,
}: {
  draftTitle: string | undefined;
  onClose: () => void;
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
