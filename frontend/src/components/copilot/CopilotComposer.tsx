/**
 * COPILOT-02.i — textarea + send button.
 *
 * Auto-growing textarea (up to a reasonable cap) so the composer
 * feels right for both "summarise emails" (one line) and "build me
 * a classifier that routes…" (a paragraph). Cmd/Ctrl+Enter sends;
 * plain Enter inserts a newline so users can paragraph freely.
 *
 * Disabled while a turn is streaming — we don't buffer follow-up
 * turns today. A second send while streaming would require
 * cancelling the current turn (AbortController) + queuing the next
 * message; that lands with 02.ii alongside the "stop" button.
 */

import { useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ArrowUp, Loader2 } from "lucide-react";


interface Props {
  onSend: (text: string) => void;
  streaming: boolean;
  placeholder?: string;
}

const MAX_ROWS = 12;


export function CopilotComposer({ onSend, streaming, placeholder }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const disabled = streaming || !value.trim();

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming) return;
    onSend(trimmed);
    setValue("");
    // Reset textarea height after submit.
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    // Auto-grow: set to auto first to measure the natural scrollHeight,
    // then clamp against MAX_ROWS so huge pastes don't eat the canvas.
    const el = e.target;
    el.style.height = "auto";
    const lineHeight = parseInt(
      getComputedStyle(el).lineHeight || "20",
      10,
    );
    const maxHeight = lineHeight * MAX_ROWS;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  };

  return (
    <div className="border-t bg-background">
      <div className="flex items-end gap-2 p-3">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          rows={2}
          placeholder={placeholder ?? "Describe what you want to build, modify, or debug…"}
          disabled={streaming}
          className="flex-1 min-w-0 resize-none rounded-md border bg-background px-3 py-2 text-sm leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-60"
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={disabled}
          className="inline-flex items-center justify-center h-9 w-9 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
          title={streaming ? "Agent is responding — wait for completion" : "Send (⌘↵)"}
          aria-label="Send message"
        >
          {streaming ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </button>
      </div>
      <p className="px-3 pb-2 text-[10px] text-muted-foreground">
        <kbd className="px-1 py-0.5 rounded border bg-muted text-[10px]">⌘↵</kbd>{" "}
        to send · <kbd className="px-1 py-0.5 rounded border bg-muted text-[10px]">↵</kbd>{" "}
        for a new line
      </p>
    </div>
  );
}
