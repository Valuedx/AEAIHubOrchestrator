"use client"

import { forwardRef, type HTMLAttributes } from "react"
import { cn } from "@/lib/utils"

/**
 * Lightweight scroll container that uses **native** scrollbars styled via
 * the global webkit / Firefox rules in index.css.
 *
 * Usage:
 *   <ScrollArea className="flex-1">…</ScrollArea>
 *
 * The component renders a wrapper div (relative, overflow-hidden) around an
 * inner viewport div that carries `overflow-y: auto`.  This lets the native
 * scrollbar track + thumb styles defined in `index.css` apply directly,
 * giving a consistent, always-visible scrollbar across all panels.
 */
const ScrollArea = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    data-slot="scroll-area"
    className={cn("relative overflow-hidden min-h-0", className)}
    {...props}
  >
    <div
      data-slot="scroll-area-viewport"
      className="h-full w-full overflow-y-auto overflow-x-hidden rounded-[inherit]"
      style={{ maxHeight: "100%" }}
    >
      {children}
    </div>
  </div>
))
ScrollArea.displayName = "ScrollArea"

/* ScrollBar is kept as a no-op export so existing imports don't break. */
function ScrollBar() {
  return null
}

export { ScrollArea, ScrollBar }
