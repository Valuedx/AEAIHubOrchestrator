/**
 * DV-03 — sticky note canvas element.
 *
 * Not an executable node: no handles, no ports, no dispatch. Acts as
 * inline documentation for the workflow — text + a preset colour.
 * Resizable via React Flow's NodeResizer. Inline-editable: click to
 * type. Double-click the header colour swatch to cycle colours.
 *
 * Deletion uses the same React Flow default (Delete / Backspace with
 * the node selected) as agentic nodes.
 */

import { memo, useCallback, useState } from "react";
import { NodeResizer, type NodeProps } from "@xyflow/react";
import { Palette, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useFlowStore } from "@/store/flowStore";
import {
  STICKY_NOTE_COLORS,
  type StickyNoteColor,
  type StickyNoteData,
} from "@/types/stickyNote";

// Keep as const-outside-render so the className string doesn't recreate
// on every keystroke — React Flow performance guideline.
const COLOR_STYLES: Record<StickyNoteColor, string> = {
  yellow:
    "bg-yellow-100 border-yellow-300 text-yellow-900 dark:bg-yellow-950/40 dark:border-yellow-900/60 dark:text-yellow-100",
  blue:
    "bg-blue-100 border-blue-300 text-blue-900 dark:bg-blue-950/40 dark:border-blue-900/60 dark:text-blue-100",
  green:
    "bg-green-100 border-green-300 text-green-900 dark:bg-green-950/40 dark:border-green-900/60 dark:text-green-100",
  pink:
    "bg-pink-100 border-pink-300 text-pink-900 dark:bg-pink-950/40 dark:border-pink-900/60 dark:text-pink-100",
  purple:
    "bg-purple-100 border-purple-300 text-purple-900 dark:bg-purple-950/40 dark:border-purple-900/60 dark:text-purple-100",
  grey:
    "bg-zinc-100 border-zinc-300 text-zinc-900 dark:bg-zinc-800/70 dark:border-zinc-700 dark:text-zinc-100",
};


function StickyNoteComponent({ id, data, selected }: NodeProps) {
  const nodeData = data as unknown as StickyNoteData;
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const deleteNode = useFlowStore((s) => s.deleteNode);
  const [localText, setLocalText] = useState(nodeData.text ?? "");

  const colorClass = COLOR_STYLES[nodeData.color] ?? COLOR_STYLES.yellow;

  const commitText = useCallback(() => {
    if (localText !== nodeData.text) {
      updateNodeData(id, { text: localText } as Partial<StickyNoteData>);
    }
  }, [id, localText, nodeData.text, updateNodeData]);

  const cycleColor = useCallback(() => {
    const idx = STICKY_NOTE_COLORS.indexOf(nodeData.color);
    const next = STICKY_NOTE_COLORS[(idx + 1) % STICKY_NOTE_COLORS.length];
    updateNodeData(id, { color: next } as Partial<StickyNoteData>);
  }, [id, nodeData.color, updateNodeData]);

  return (
    <div
      className={cn(
        "rounded-md border-2 shadow-sm transition-shadow",
        "flex flex-col",
        selected ? "ring-2 ring-primary/60" : "",
        colorClass,
      )}
      style={{ width: "100%", height: "100%" }}
    >
      <NodeResizer
        minWidth={120}
        minHeight={80}
        isVisible={selected}
        handleClassName="!bg-primary !border-background"
        lineClassName="!border-primary/40"
      />

      <div className="flex items-center justify-between px-2 py-1 border-b border-current/10">
        <button
          type="button"
          onClick={cycleColor}
          className="p-1 rounded hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
          title="Cycle sticky colour"
          aria-label="Change colour"
        >
          <Palette className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={() => deleteNode(id)}
          className="p-1 rounded hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
          title="Delete sticky note"
          aria-label="Delete"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>

      <textarea
        value={localText}
        onChange={(e) => setLocalText(e.target.value)}
        onBlur={commitText}
        placeholder="Write a note…"
        // nodrag is a React Flow convention: elements with this class
        // don't trigger node drag when interacted with.
        className={cn(
          "nodrag nowheel flex-1 resize-none outline-none",
          "bg-transparent text-xs p-2 placeholder:text-current/50",
          "font-sans leading-snug",
        )}
      />
    </div>
  );
}

export const StickyNote = memo(StickyNoteComponent);
