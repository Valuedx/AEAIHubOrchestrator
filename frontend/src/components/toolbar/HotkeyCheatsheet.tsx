/**
 * DV-06 — Hotkey cheatsheet modal.
 *
 * Lists every canvas-level keyboard shortcut so operators can discover
 * them without spelunking through source. Opened via ``?`` (matches
 * GitHub / Linear / most SaaS tools) or the toolbar Keyboard icon.
 *
 * Keep the list in this file — it's the one place both the handlers
 * (implemented across App/FlowCanvas) and the help dialog need to
 * agree. Adding a new shortcut? Register it in the source where it
 * fires AND add a row here.
 */

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface Shortcut {
  keys: string[];
  label: string;
}

interface Section {
  title: string;
  items: Shortcut[];
}

const SECTIONS: Section[] = [
  {
    title: "Canvas",
    items: [
      { keys: ["Shift", "S"], label: "Add sticky note at viewport centre" },
      { keys: ["1"], label: "Fit view to workflow" },
      { keys: ["Tab"], label: "Toggle node palette" },
      { keys: ["Delete"], label: "Delete selected node(s) or edge(s)" },
      { keys: ["Backspace"], label: "Delete selected node(s) or edge(s)" },
    ],
  },
  {
    title: "History",
    items: [
      { keys: ["Ctrl", "Z"], label: "Undo" },
      { keys: ["Ctrl", "Y"], label: "Redo" },
      { keys: ["Ctrl", "Shift", "Z"], label: "Redo (alt)" },
    ],
  },
  {
    title: "Help",
    items: [
      { keys: ["?"], label: "Open this cheatsheet" },
      { keys: ["Esc"], label: "Close dialogs / deselect" },
    ],
  },
];

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex items-center px-1.5 py-0.5 rounded border bg-muted text-[11px] font-mono leading-none">
      {children}
    </kbd>
  );
}

interface HotkeyCheatsheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function HotkeyCheatsheet({ open, onOpenChange }: HotkeyCheatsheetProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {SECTIONS.map((section) => (
            <div key={section.title}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                {section.title}
              </h3>
              <ul className="space-y-1.5">
                {section.items.map((sc) => (
                  <li
                    key={sc.keys.join("+") + sc.label}
                    className="flex items-center justify-between gap-3 text-sm"
                  >
                    <span className="text-foreground">{sc.label}</span>
                    <span className="flex items-center gap-1 shrink-0">
                      {sc.keys.map((k, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <span className="text-muted-foreground">+</span>}
                          <Kbd>{k}</Kbd>
                        </span>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
