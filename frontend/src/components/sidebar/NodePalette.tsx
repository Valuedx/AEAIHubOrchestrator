import { useState, type DragEvent } from "react";
import {
  Webhook,
  Clock,
  Brain,
  Repeat,
  Wrench,
  Globe,
  UserCheck,
  GitBranch,
  GitMerge,
  Route,
  History,
  Save,
  RefreshCw,
  ChevronRight,
  Search,
  X,
  Database,
  Bell,
  Target,
  ListFilter,
  type LucideIcon,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { NODE_PALETTE, type PaletteItem, type NodeCategory } from "@/types/nodes";
import { cn } from "@/lib/utils";

const ICON_MAP: Record<string, LucideIcon> = {
  webhook: Webhook,
  clock: Clock,
  brain: Brain,
  repeat: Repeat,
  wrench: Wrench,
  globe: Globe,
  "user-check": UserCheck,
  "git-branch": GitBranch,
  "git-merge": GitMerge,
  route: Route,
  history: History,
  save: Save,
  "refresh-cw": RefreshCw,
  database: Database,
  bell: Bell,
  target: Target,
  "list-filter": ListFilter,
};

const CATEGORY_META: Record<NodeCategory, { label: string; color: string }> = {
  trigger: { label: "Triggers", color: "text-amber-600 dark:text-amber-400" },
  agent: { label: "AI Agents", color: "text-violet-600 dark:text-violet-400" },
  action: { label: "Actions", color: "text-sky-600 dark:text-sky-400" },
  logic: { label: "Logic", color: "text-emerald-600 dark:text-emerald-400" },
  knowledge: { label: "Knowledge", color: "text-teal-600 dark:text-teal-400" },
  notification: { label: "Notifications", color: "text-rose-600 dark:text-rose-400" },
  nlp: { label: "NLP", color: "text-indigo-600 dark:text-indigo-400" },
};

const CATEGORIES: NodeCategory[] = ["trigger", "agent", "action", "logic", "knowledge", "notification", "nlp"];

function DraggableItem({ item }: { item: PaletteItem }) {
  const Icon = ICON_MAP[item.icon] || Wrench;

  const onDragStart = (e: DragEvent) => {
    e.dataTransfer.setData(
      "application/reactflow",
      JSON.stringify({
        nodeCategory: item.nodeCategory,
        label: item.label,
        defaultConfig: { ...item.defaultConfig, icon: item.icon },
      }),
    );
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="flex items-center gap-2.5 rounded-md border bg-card px-3 py-2 cursor-grab active:cursor-grabbing hover:bg-accent transition-colors"
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <p className="text-sm font-medium leading-tight truncate">{item.label}</p>
        <p className="text-[11px] text-muted-foreground leading-tight truncate">
          {item.description}
        </p>
      </div>
    </div>
  );
}

interface NodePaletteProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function NodePalette({ collapsed, onToggle }: NodePaletteProps) {
  const [openCategories, setOpenCategories] = useState<Set<NodeCategory>>(
    new Set(CATEGORIES),
  );
  const [search, setSearch] = useState("");

  const toggle = (cat: NodeCategory) => {
    setOpenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const q = search.trim().toLowerCase();
  const filterItems = (items: PaletteItem[]) =>
    q
      ? items.filter(
          (i) =>
            i.label.toLowerCase().includes(q) ||
            i.description.toLowerCase().includes(q),
        )
      : items;

  if (collapsed) {
    return (
      <div className="flex flex-col items-center w-12 border-r bg-sidebar py-3 gap-2">
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-accent transition-colors"
          title="Expand palette"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <Separator />
        {NODE_PALETTE.slice(0, 6).map((item) => {
          const Icon = ICON_MAP[item.icon] || Wrench;
          return (
            <div
              key={item.label}
              draggable
              onDragStart={(e: DragEvent<HTMLDivElement>) => {
                e.dataTransfer.setData(
                  "application/reactflow",
                  JSON.stringify({
                    nodeCategory: item.nodeCategory,
                    label: item.label,
                    defaultConfig: { ...item.defaultConfig, icon: item.icon },
                  }),
                );
                e.dataTransfer.effectAllowed = "move";
              }}
              className="p-1.5 rounded-md cursor-grab hover:bg-accent transition-colors"
              title={item.label}
            >
              <Icon className="h-4 w-4 text-muted-foreground" />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="flex flex-col w-64 border-r bg-sidebar">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold">Node Palette</h2>
        <button
          onClick={onToggle}
          className="p-1 rounded-md hover:bg-accent transition-colors"
          title="Collapse palette"
        >
          <ChevronRight className="h-4 w-4 rotate-180" />
        </button>
      </div>
      <Separator />

      {/* Search input */}
      <div className="px-3 py-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search nodes…"
            className="w-full rounded-md border border-input bg-background pl-8 pr-7 py-1.5 text-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              title="Clear search"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
      <Separator />

      <ScrollArea className="flex-1 px-3 py-2">
        <div className="space-y-1">
          {CATEGORIES.map((cat) => {
            const meta = CATEGORY_META[cat];
            const allItems = NODE_PALETTE.filter((i) => i.nodeCategory === cat);
            const items = filterItems(allItems);

            // Hide entire category when search has no matches in it
            if (q && items.length === 0) return null;

            return (
              <Collapsible
                key={cat}
                // Auto-expand categories that have search matches
                open={q ? true : openCategories.has(cat)}
                onOpenChange={() => !q && toggle(cat)}
              >
                <CollapsibleTrigger className="flex items-center gap-2 w-full rounded-md px-2 py-1.5 text-sm font-medium hover:bg-accent transition-colors">
                  <ChevronRight
                    className={cn(
                      "h-3.5 w-3.5 transition-transform",
                      (q || openCategories.has(cat)) && "rotate-90",
                    )}
                  />
                  <span className={meta.color}>{meta.label}</span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {q ? `${items.length}/${allItems.length}` : allItems.length}
                  </span>
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-1.5 py-1.5 pl-2">
                  {items.map((item) => (
                    <DraggableItem key={item.label} item={item} />
                  ))}
                </CollapsibleContent>
              </Collapsible>
            );
          })}

          {/* Empty state when nothing matches */}
          {q && CATEGORIES.every((cat) => filterItems(NODE_PALETTE.filter((i) => i.nodeCategory === cat)).length === 0) && (
            <p className="text-xs text-muted-foreground text-center py-4">
              No nodes match "{search}"
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
