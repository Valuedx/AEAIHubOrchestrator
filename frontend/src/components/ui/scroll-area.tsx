"use client"

import { ScrollArea as ScrollAreaPrimitive } from "@base-ui/react/scroll-area"

import { cn } from "@/lib/utils"

function ScrollArea({
  className,
  children,
  ...props
}: ScrollAreaPrimitive.Root.Props) {
  return (
    <ScrollAreaPrimitive.Root
      data-slot="scroll-area"
      className={cn("relative", className)}
      {...props}
    >
      <ScrollAreaPrimitive.Viewport
        data-slot="scroll-area-viewport"
        className="size-full rounded-[inherit] transition-[color,box-shadow] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1"
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollBar />
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  )
}

function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: ScrollAreaPrimitive.Scrollbar.Props) {
  return (
    <ScrollAreaPrimitive.Scrollbar
      data-slot="scroll-area-scrollbar"
      data-orientation={orientation}
      orientation={orientation}
      // Track is always visible (not overlay-hover). Windows users —
      // our primary audience — expect a persistent scroll affordance
      // so they can see at a glance *that* a region is scrollable.
      // The thumb darkens on hover/drag for affordance.
      className={cn(
        "flex touch-none select-none transition-colors",
        "data-vertical:h-full data-vertical:w-2.5",
        "data-horizontal:h-2.5 data-horizontal:w-full data-horizontal:flex-col",
        "bg-muted/40 hover:bg-muted/60 data-scrolling:bg-muted/60",
        className
      )}
      {...props}
    >
      <ScrollAreaPrimitive.Thumb
        data-slot="scroll-area-thumb"
        className={cn(
          "relative flex-1 rounded-full bg-muted-foreground/40",
          "hover:bg-muted-foreground/60 transition-colors",
        )}
      />
    </ScrollAreaPrimitive.Scrollbar>
  )
}

export { ScrollArea, ScrollBar }
