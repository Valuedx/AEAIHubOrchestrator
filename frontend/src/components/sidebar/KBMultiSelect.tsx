import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { KBOut } from "@/lib/api";

interface Props {
  selected: string[];
  onChange: (ids: string[]) => void;
}

export function KBMultiSelect({ selected, onChange }: Props) {
  const [kbs, setKbs] = useState<KBOut[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listKnowledgeBases()
      .then(setKbs)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="text-xs text-muted-foreground">Loading knowledge bases…</p>;
  }

  if (kbs.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No knowledge bases found. Create one via the toolbar Knowledge Bases button.
      </p>
    );
  }

  const toggle = (id: string) => {
    if (selected.includes(id)) {
      onChange(selected.filter((x) => x !== id));
    } else {
      onChange([...selected, id]);
    }
  };

  return (
    <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
      {selected.length === 0 && (
        <p className="text-[10px] text-muted-foreground italic">
          No knowledge bases selected
        </p>
      )}
      {kbs.map((kb) => {
        const checked = selected.includes(kb.id);
        return (
          <label
            key={kb.id}
            className="flex items-start gap-2 cursor-pointer group"
          >
            <input
              type="checkbox"
              className="mt-0.5 shrink-0"
              checked={checked}
              onChange={() => toggle(kb.id)}
            />
            <div className="min-w-0 flex-1">
              <span
                className={`text-xs ${checked ? "text-foreground" : "text-muted-foreground"} group-hover:text-foreground transition-colors`}
              >
                {kb.name}
              </span>
              <div className="flex items-center gap-1.5 mt-0.5">
                <Badge variant="outline" className="text-[9px] px-1 py-0">
                  {kb.document_count} docs
                </Badge>
                <Badge variant="outline" className="text-[9px] px-1 py-0">
                  {kb.vector_store}
                </Badge>
              </div>
            </div>
          </label>
        );
      })}
    </div>
  );
}
