/**
 * DV-03 — sticky notes on the canvas.
 *
 * Stickies are canvas annotations, not executable nodes. They live
 * alongside ``agenticNode`` entries in ``flowStore.nodes`` but are
 * discriminated by ``Node.type === "stickyNote"``. The backend's
 * ``parse_graph`` filters these out so they never enter the execution
 * ready queue. Consumers that iterate nodes (validateWorkflow,
 * computeNodeStatuses, PropertyInspector) must check ``node.type`` and
 * skip stickies.
 */

export type StickyNoteColor =
  | "yellow"
  | "blue"
  | "green"
  | "pink"
  | "purple"
  | "grey";

export const STICKY_NOTE_COLORS: StickyNoteColor[] = [
  "yellow",
  "blue",
  "green",
  "pink",
  "purple",
  "grey",
];

export interface StickyNoteData {
  [key: string]: unknown;
  text: string;
  color: StickyNoteColor;
}

export const STICKY_NOTE_DEFAULT_DATA: StickyNoteData = {
  text: "",
  color: "yellow",
};

/** Runtime-safe discriminator. Use at consumer boundaries. */
export function isStickyNode(node: { type?: string }): boolean {
  return node.type === "stickyNote";
}
