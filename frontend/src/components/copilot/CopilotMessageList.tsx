/**
 * COPILOT-02.i — scrollable chat history.
 *
 * Renders two kinds of items in chronological order:
 *
 *  - User turns (local state, always user-authored — a simple
 *    right-aligned bubble).
 *  - Agent events streamed back from the current turn, plus any
 *    prior-turn events replayed from ``copilot_turns`` rows via
 *    ``api.listCopilotTurns`` (future — today we show only the
 *    live stream + in-memory user bubbles; history reload is
 *    cheap to add later via the same component without a shape
 *    change).
 *
 * The list auto-scrolls to bottom whenever items change so the
 * most recent output stays visible during streaming. An explicit
 * scroll-to-bottom button appears when the user scrolls up past
 * a threshold — lets them review earlier turns without the view
 * fighting them.
 *
 * User feedback that shaped this: "panels should be large enough
 * and visible". The bubble column uses ``max-w-full`` with generous
 * padding + relaxed line-height so prose doesn't feel cramped at
 * the panel's 440 px default width.
 */

import { useEffect, useRef, useState } from "react";
import { ArrowDown, MessageSquare } from "lucide-react";
import type { CopilotAgentEvent } from "@/lib/api";
import { CopilotEventCard } from "./CopilotToolResultCard";

export interface ChatItem {
  id: string;
  /**
   * ``user`` → the message the user typed (rendered as a right-aligned bubble).
   * ``event`` → one streamed event from the agent (dispatched by CopilotEventCard).
   */
  kind: "user" | "event";
  userText?: string;
  event?: CopilotAgentEvent;
}

interface Props {
  items: ChatItem[];
  /** True when a turn is in flight — renders a subtle "thinking" indicator. */
  streaming: boolean;
}

const SCROLL_BOTTOM_THRESHOLD_PX = 80;

export function CopilotMessageList({ items, streaming }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [autoStick, setAutoStick] = useState(true);

  // Auto-scroll to bottom when new items arrive, unless the user has
  // manually scrolled up (in which case the "scroll to latest" button
  // does the jump explicitly).
  useEffect(() => {
    if (!autoStick) return;
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [items.length, streaming, autoStick]);

  const onScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const distanceFromBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight;
    setAutoStick(distanceFromBottom < SCROLL_BOTTOM_THRESHOLD_PX);
  };

  const jumpToBottom = () => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setAutoStick(true);
  };

  if (items.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="relative flex-1 min-h-0">
      <div
        ref={containerRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto px-4 py-4 space-y-3"
      >
        {items.map((item) =>
          item.kind === "user" ? (
            <UserBubble key={item.id} text={item.userText ?? ""} />
          ) : (
            <CopilotEventCard key={item.id} event={item.event!} />
          )
        )}
        {streaming && <ThinkingIndicator />}
      </div>

      {!autoStick && (
        <button
          type="button"
          onClick={jumpToBottom}
          className="absolute bottom-3 right-3 inline-flex items-center gap-1 rounded-full border bg-background/90 px-2.5 py-1 text-[11px] shadow-sm hover:bg-background transition-colors"
        >
          <ArrowDown className="h-3 w-3" />
          Jump to latest
        </button>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Empty + streaming affordances
// ---------------------------------------------------------------------------


function EmptyState() {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center p-6 text-center">
      <MessageSquare className="h-7 w-7 text-muted-foreground mb-3" />
      <p className="text-sm text-muted-foreground max-w-sm leading-relaxed">
        Describe what you want to build, modify, or test. The copilot
        will ask clarifying questions, draft the workflow, and explain
        what it built before you accept.
      </p>
      <div className="mt-5 space-y-1.5 text-[11px] text-muted-foreground text-left max-w-sm w-full">
        <p className="font-medium text-foreground">Try:</p>
        <ul className="space-y-1 pl-4 list-disc">
          <li>"Build a flow that reads a Slack message and summarises it via email."</li>
          <li>"Add a human approval gate before the Jira classifier routes."</li>
          <li>"Why did node_3 fail in the last run?"</li>
        </ul>
      </div>
    </div>
  );
}


function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1 items-end">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        You
      </span>
      <div className="rounded-lg rounded-tr-sm bg-primary/10 text-foreground px-3.5 py-2.5 text-sm leading-relaxed max-w-[95%] whitespace-pre-wrap break-words">
        {text}
      </div>
    </div>
  );
}


function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2 text-[11px] text-muted-foreground pl-2">
      <span className="inline-flex items-center gap-0.5">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60 animate-pulse [animation-delay:-200ms]" />
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60 animate-pulse [animation-delay:-100ms]" />
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60 animate-pulse" />
      </span>
      Copilot is working…
    </div>
  );
}
