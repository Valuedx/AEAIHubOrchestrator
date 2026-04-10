/**
 * ExpressionInput — a text input (or textarea) with an autocomplete dropdown
 * that suggests upstream node output variables.
 *
 * Features:
 *  - Fixed-position dropdown (not clipped by ScrollArea overflow)
 *  - Cursor-aware token detection: filters by the word under the cursor
 *  - Keyboard navigation: ArrowUp/Down to move, Enter/Tab to insert, Esc to close
 *  - Grouped suggestions with group headers
 *  - Works in both single-line (Input) and multi-line (Textarea) mode
 */

import {
  useState,
  useRef,
  useCallback,
  useEffect,
  type KeyboardEvent,
  type ChangeEvent,
} from "react";
import { createPortal } from "react-dom";
import { Zap } from "lucide-react";
import type { ExpressionVariable } from "@/lib/expressionVariables";
import { getCurrentToken, insertAtCursor } from "@/lib/expressionVariables";
import { cn } from "@/lib/utils";

interface ExpressionInputProps {
  value: string;
  onChange: (value: string) => void;
  suggestions: ExpressionVariable[];
  multiline?: boolean;
  placeholder?: string;
  rows?: number;
  className?: string;
}

interface DropdownPosition {
  top: number;
  left: number;
  width: number;
}

export function ExpressionInput({
  value,
  onChange,
  suggestions,
  multiline = false,
  placeholder,
  rows = 4,
  className,
}: ExpressionInputProps) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [dropdownPos, setDropdownPos] = useState<DropdownPosition | null>(null);
  const inputRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // ── Filtering ────────────────────────────────────────────────────────────
  const filtered = filter
    ? suggestions.filter(
        (s) =>
          s.value.toLowerCase().includes(filter.toLowerCase()) ||
          s.label.toLowerCase().includes(filter.toLowerCase()),
      )
    : suggestions;
  const capped = filtered.slice(0, 30);

  // ── Dropdown position (fixed, relative to viewport) ───────────────────
  const updatePosition = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setDropdownPos({
      top: rect.bottom + 4,
      left: rect.left,
      width: rect.width,
    });
  }, []);

  // ── Open / close ─────────────────────────────────────────────────────────
  const handleFocus = () => {
    updatePosition();
    setOpen(true);
  };

  const handleBlur = () => {
    // Small delay so a click on a suggestion item can register first
    setTimeout(() => setOpen(false), 150);
  };

  // ── Input change ──────────────────────────────────────────────────────────
  const handleChange = (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const newVal = e.target.value;
    const cursorPos = e.target.selectionStart ?? newVal.length;
    onChange(newVal);
    const { token } = getCurrentToken(newVal, cursorPos);
    setFilter(token);
    setActiveIndex(0);
    updatePosition();
    setOpen(true);
  };

  // ── Insert suggestion at cursor ──────────────────────────────────────────
  const insertSuggestion = useCallback(
    (suggestion: ExpressionVariable) => {
      const el = inputRef.current;
      if (!el) return;
      const cursorPos = el.selectionStart ?? value.length;
      const { newValue, newCursorPos } = insertAtCursor(value, cursorPos, suggestion.value);
      onChange(newValue);
      setOpen(false);
      setFilter("");
      // Restore cursor after React re-render
      requestAnimationFrame(() => {
        el.focus();
        el.setSelectionRange(newCursorPos, newCursorPos);
      });
    },
    [value, onChange],
  );

  // ── Keyboard navigation ──────────────────────────────────────────────────
  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (!open || capped.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, capped.length - 1));
      scrollActiveIntoView();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
      scrollActiveIntoView();
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (capped[activeIndex]) {
        e.preventDefault();
        insertSuggestion(capped[activeIndex]);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  const scrollActiveIntoView = () => {
    requestAnimationFrame(() => {
      dropdownRef.current
        ?.querySelector(`[data-active="true"]`)
        ?.scrollIntoView({ block: "nearest" });
    });
  };

  // ── Close on scroll / resize ─────────────────────────────────────────────
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  // ── Render grouped dropdown via portal ───────────────────────────────────
  const renderDropdown = () => {
    if (!open || !dropdownPos) return null;

    let lastGroup = "";
    return createPortal(
      <div
        ref={dropdownRef}
        style={{
          position: "fixed",
          top: dropdownPos.top,
          left: dropdownPos.left,
          width: dropdownPos.width,
          zIndex: 9999,
        }}
        className="bg-popover border border-border rounded-md shadow-lg max-h-52 overflow-y-auto text-sm"
        onMouseDown={(e) => e.preventDefault()} // prevent blur before click
      >
        {capped.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">No matches</p>
        ) : (
          capped.map((s, i) => {
            const showGroup = s.group !== lastGroup;
            lastGroup = s.group;
            return (
              <div key={`${s.value}-${i}`}>
                {showGroup && (
                  <p className="px-3 pt-2 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground select-none">
                    {s.group}
                  </p>
                )}
                <button
                  data-active={i === activeIndex}
                  className={cn(
                    "w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-accent transition-colors",
                    i === activeIndex && "bg-accent",
                  )}
                  onMouseEnter={() => setActiveIndex(i)}
                  onClick={() => insertSuggestion(s)}
                >
                  <Zap className="h-3 w-3 text-muted-foreground shrink-0" />
                  <span className="font-mono truncate">{s.label}</span>
                </button>
              </div>
            );
          })
        )}
      </div>,
      document.body,
    );
  };

  const sharedProps = {
    ref: inputRef,
    value,
    onChange: handleChange,
    onFocus: handleFocus,
    onBlur: handleBlur,
    onKeyDown: handleKeyDown,
    placeholder,
    className: cn(
      "flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
      "ring-offset-background placeholder:text-muted-foreground",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      "disabled:cursor-not-allowed disabled:opacity-50",
      "font-mono",
      className,
    ),
  };

  return (
    <>
      {multiline ? (
        <textarea {...sharedProps} rows={rows} />
      ) : (
        <input {...sharedProps} type="text" />
      )}
      {renderDropdown()}
    </>
  );
}
