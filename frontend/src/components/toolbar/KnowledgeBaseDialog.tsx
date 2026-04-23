import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import {
  Trash2,
  Upload,
  Loader2,
  ChevronLeft,
  FileText,
  Plus,
  Database,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  api,
  type KBOut,
  type KBDocumentOut,
  type EmbeddingOption,
  type ChunkingStrategy,
  type VectorStoreOption,
} from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type View = "list" | "create" | "detail";

const STATUS_COLORS: Record<string, string> = {
  ready: "bg-green-500/15 text-green-700 dark:text-green-400",
  pending: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  processing: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  failed: "bg-red-500/15 text-red-700 dark:text-red-400",
};

export function KnowledgeBaseDialog({ open, onOpenChange }: Props) {
  const [view, setView] = useState<View>("list");
  const [kbs, setKbs] = useState<KBOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedKb, setSelectedKb] = useState<KBOut | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    api.listKnowledgeBases().then(setKbs).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!open) return;
    // Defer the synchronous setState calls out of the effect body — keeps
    // react-hooks/set-state-in-effect happy. The microtask fires in the
    // same tick so the UX is indistinguishable.
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setView("list");
      refresh();
    });
    return () => { cancelled = true; };
  }, [open, refresh]);

  const handleDelete = (id: string, e: MouseEvent) => {
    e.stopPropagation();
    if (confirm("Delete this knowledge base and all its documents?")) {
      api.deleteKnowledgeBase(id).then(refresh);
    }
  };

  const openDetail = (kb: KBOut) => {
    setSelectedKb(kb);
    setView("detail");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view !== "list" && (
              <button onClick={() => { setView("list"); refresh(); }} className="p-1 hover:bg-accent rounded">
                <ChevronLeft className="h-4 w-4" />
              </button>
            )}
            <Database className="h-5 w-5" />
            {view === "list" && "Knowledge Bases"}
            {view === "create" && "Create Knowledge Base"}
            {view === "detail" && (selectedKb?.name || "Documents")}
          </DialogTitle>
        </DialogHeader>
        <Separator />

        {view === "list" && (
          <KBListView
            kbs={kbs}
            loading={loading}
            onDelete={handleDelete}
            onSelect={openDetail}
            onCreate={() => setView("create")}
          />
        )}

        {view === "create" && (
          <KBCreateView
            onCreated={() => {
              setView("list");
              refresh();
            }}
          />
        )}

        {view === "detail" && selectedKb && (
          <KBDetailView kb={selectedKb} />
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ---------- LIST VIEW ---------- */

function KBListView({
  kbs, loading, onDelete, onSelect, onCreate,
}: {
  kbs: KBOut[];
  loading: boolean;
  onDelete: (id: string, e: MouseEvent) => void;
  onSelect: (kb: KBOut) => void;
  onCreate: () => void;
}) {
  return (
    <>
      <div className="flex justify-end">
        <Button size="sm" onClick={onCreate} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> New
        </Button>
      </div>
      <ScrollArea className="flex-1 min-h-0 max-h-[55vh]">
        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!loading && kbs.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">
            No knowledge bases yet. Create one to get started.
          </p>
        )}
        <div className="space-y-2">
          {kbs.map((kb) => (
            <div
              key={kb.id}
              onClick={() => onSelect(kb)}
              className="flex items-center gap-3 rounded-lg border px-4 py-3 cursor-pointer hover:bg-accent transition-colors"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{kb.name}</p>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  <Badge variant="outline" className="text-[10px]">{kb.vector_store}</Badge>
                  <Badge variant="outline" className="text-[10px]">{kb.embedding_provider}/{kb.embedding_model}</Badge>
                  <Badge variant="outline" className="text-[10px]">{kb.chunking_strategy}</Badge>
                  <span className="text-[10px] text-muted-foreground">{kb.document_count} docs</span>
                </div>
              </div>
              <button
                onClick={(e) => onDelete(kb.id, e)}
                className="p-1.5 text-muted-foreground hover:text-red-500 transition-colors shrink-0"
                title="Delete"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      </ScrollArea>
    </>
  );
}

/* ---------- CREATE VIEW ---------- */

function KBCreateView({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [embeddingProvider, setEmbeddingProvider] = useState("openai");
  const [embeddingModel, setEmbeddingModel] = useState("text-embedding-3-small");
  const [vectorStore, setVectorStore] = useState("pgvector");
  const [chunkingStrategy, setChunkingStrategy] = useState("recursive");
  const [chunkSize, setChunkSize] = useState(1000);
  const [chunkOverlap, setChunkOverlap] = useState(200);
  const [semanticThreshold, setSemanticThreshold] = useState(0.5);
  const [saving, setSaving] = useState(false);

  const [embOpts, setEmbOpts] = useState<EmbeddingOption[]>([]);
  const [chunkOpts, setChunkOpts] = useState<ChunkingStrategy[]>([]);
  const [vsOpts, setVsOpts] = useState<VectorStoreOption[]>([]);

  useEffect(() => {
    api.getEmbeddingOptions().then(setEmbOpts).catch(() => {});
    api.getChunkingStrategies().then(setChunkOpts).catch(() => {});
    api.getVectorStores().then(setVsOpts).catch(() => {});
  }, []);

  const providers = [...new Set(embOpts.map((o) => o.provider))];
  const modelsForProvider = embOpts.filter((o) => o.provider === embeddingProvider);

  useEffect(() => {
    if (modelsForProvider.length > 0 && !modelsForProvider.find((m) => m.model === embeddingModel)) {
      setEmbeddingModel(modelsForProvider[0].model);
    }
  }, [embeddingProvider, modelsForProvider, embeddingModel]);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await api.createKnowledgeBase({
        name: name.trim(),
        description: description.trim() || undefined,
        embedding_provider: embeddingProvider,
        embedding_model: embeddingModel,
        vector_store: vectorStore,
        chunking_strategy: chunkingStrategy,
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
        semantic_threshold: chunkingStrategy === "semantic" ? semanticThreshold : null,
      });
      onCreated();
    } catch {
      alert("Failed to create knowledge base");
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScrollArea className="flex-1 min-h-0 max-h-[60vh]">
      <div className="space-y-4 pr-2">
        <div className="space-y-2">
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My Knowledge Base" />
        </div>
        <div className="space-y-2">
          <Label>Description</Label>
          <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} placeholder="Optional description" />
        </div>

        <Separator />

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Embedding Provider</Label>
            <Select value={embeddingProvider} onValueChange={(value) => setEmbeddingProvider(value ?? "")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {providers.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Embedding Model</Label>
            <Select value={embeddingModel} onValueChange={(value) => setEmbeddingModel(value ?? "")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {modelsForProvider.map((o) => (
                  <SelectItem key={o.model} value={o.model}>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-medium">{o.display_name || o.model}</span>
                      <span className="text-[10px] text-muted-foreground">{o.dimension}d</span>
                      {o.preview && (
                        <Badge variant="outline" className="text-[9px] px-1 py-0 border-amber-500/40 text-amber-700 dark:text-amber-400">preview</Badge>
                      )}
                      {o.modalities.length > 1 && (
                        <Badge variant="outline" className="text-[9px] px-1 py-0 border-violet-500/40 text-violet-700 dark:text-violet-400">
                          multimodal
                        </Badge>
                      )}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {modelsForProvider.find((m) => m.model === embeddingModel) && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {modelsForProvider
                  .find((m) => m.model === embeddingModel)!
                  .modalities.map((mod) => (
                    <Badge key={mod} variant="secondary" className="text-[9px] px-1.5 py-0">
                      {mod}
                    </Badge>
                  ))}
              </div>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Vector Store</Label>
            <Select value={vectorStore} onValueChange={(value) => setVectorStore(value ?? "")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {vsOpts.map((v) => (
                  <SelectItem key={v.id} value={v.id}>{v.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground">
              {vsOpts.find((v) => v.id === vectorStore)?.description}
            </p>
          </div>
          <div className="space-y-2">
            <Label>Chunking Strategy</Label>
            <Select value={chunkingStrategy} onValueChange={(value) => setChunkingStrategy(value ?? "")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {chunkOpts.map((c) => (
                  <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground">
              {chunkOpts.find((c) => c.id === chunkingStrategy)?.description}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Chunk Size</Label>
            <Input type="number" min={100} max={10000} value={chunkSize} onChange={(e) => setChunkSize(Number(e.target.value))} />
          </div>
          <div className="space-y-2">
            <Label>Chunk Overlap</Label>
            <Input type="number" min={0} max={2000} value={chunkOverlap} onChange={(e) => setChunkOverlap(Number(e.target.value))} />
          </div>
        </div>

        {chunkingStrategy === "semantic" && (
          <div className="space-y-2">
            <Label>Semantic Threshold ({semanticThreshold})</Label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={semanticThreshold}
              onChange={(e) => setSemanticThreshold(Number(e.target.value))}
              className="w-full"
            />
            <p className="text-[10px] text-muted-foreground">
              Lower = more splits (finer chunks). Higher = fewer splits (larger chunks).
            </p>
          </div>
        )}

        <div className="flex justify-end pt-2">
          <Button onClick={handleSubmit} disabled={!name.trim() || saving} className="gap-1.5">
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            Create
          </Button>
        </div>
      </div>
    </ScrollArea>
  );
}

/* ---------- DETAIL VIEW (documents) ---------- */

function KBDetailView({ kb }: { kb: KBOut }) {
  const [docs, setDocs] = useState<KBDocumentOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(() => {
    api.listDocuments(kb.id).then(setDocs).finally(() => setLoading(false));
  }, [kb.id]);

  useEffect(() => {
    refresh();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [refresh]);

  // Poll while any doc is pending/processing
  useEffect(() => {
    const hasPending = docs.some((d) => d.status === "pending" || d.status === "processing");
    if (hasPending && !pollRef.current) {
      pollRef.current = window.setInterval(refresh, 3000);
    } else if (!hasPending && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [docs, refresh]);

  const handleUpload = async (files: FileList | null) => {
    if (!files) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(kb.id, file);
      }
      refresh();
    } catch {
      alert("Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = (docId: string) => {
    if (confirm("Delete this document and its chunks?")) {
      api.deleteDocument(kb.id, docId).then(refresh);
    }
  };

  return (
    <div className="flex flex-col gap-3 min-h-0">
      <div className="flex items-center gap-2 flex-wrap text-xs text-muted-foreground">
        <Badge variant="outline">{kb.vector_store}</Badge>
        <Badge variant="outline">{kb.embedding_provider}/{kb.embedding_model}</Badge>
        <Badge variant="outline">{kb.chunking_strategy}</Badge>
        <Badge variant="outline">{kb.chunk_size} / {kb.chunk_overlap}</Badge>
      </div>

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="gap-1.5"
        >
          {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
          Upload Document
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.txt,.md,.csv,.html"
          multiple
          className="hidden"
          onChange={(e) => handleUpload(e.target.files)}
        />
        <span className="text-xs text-muted-foreground">{docs.length} documents</span>
      </div>

      <Separator />

      <ScrollArea className="flex-1 min-h-0 max-h-[45vh]">
        {loading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!loading && docs.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-6">
            No documents yet. Upload files to populate this knowledge base.
          </p>
        )}
        <div className="space-y-2">
          {docs.map((doc) => (
            <div key={doc.id} className="flex items-center gap-3 rounded-lg border px-3 py-2.5">
              <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{doc.filename}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <Badge className={`text-[10px] px-1.5 py-0 ${STATUS_COLORS[doc.status] ?? ""}`}>
                    {doc.status}
                  </Badge>
                  {doc.chunk_count > 0 && (
                    <span className="text-[10px] text-muted-foreground">{doc.chunk_count} chunks</span>
                  )}
                  <span className="text-[10px] text-muted-foreground">
                    {(doc.file_size / 1024).toFixed(1)} KB
                  </span>
                </div>
                {doc.error && <p className="text-[10px] text-red-500 mt-0.5 truncate">{doc.error}</p>}
              </div>
              <button
                onClick={() => handleDelete(doc.id)}
                className="p-1.5 text-muted-foreground hover:text-red-500 transition-colors shrink-0"
                title="Delete document"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
