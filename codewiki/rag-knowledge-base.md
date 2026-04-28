# RAG & Knowledge Base

The RAG (Retrieval-Augmented Generation) system lets users upload documents, chunk and embed them, store vectors, and retrieve relevant context at workflow execution time. This page covers every component of the pipeline.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Ingestion Pipeline                       │
│                                                              │
│  Upload (API)  →  Parse  →  Chunk  →  Embed  →  Store       │
│  ┌──────────┐   ┌──────┐  ┌──────┐  ┌──────┐  ┌──────────┐ │
│  │ PDF/TXT/ │   │ pymu │  │recur-│  │OpenAI│  │ pgvector │ │
│  │ MD/CSV/  │──▶│ pdf  │─▶│sive/ │─▶│Google│─▶│   or     │ │
│  │ HTML     │   │ UTF-8│  │token/│  │Vertex│  │  FAISS   │ │
│  └──────────┘   └──────┘  │md/   │  └──────┘  └──────────┘ │
│                            │seman.│                          │
│                            └──────┘                          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Retrieval Pipeline                         │
│                                                              │
│  Query  →  Embed  →  Vector Search  →  Rank  →  Return      │
│  ┌──────┐  ┌──────┐  ┌────────────┐  ┌──────┐  ┌─────────┐ │
│  │ text │─▶│ same │─▶│ cosine sim │─▶│ sort │─▶│ chunks  │ │
│  │      │  │ model│  │ per KB     │  │ merge│  │ + text  │ │
│  └──────┘  └──────┘  └────────────┘  └──────┘  └─────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Embedding providers

Defined in `backend/app/engine/embedding_provider.py`, mirrored into the central [model registry](model-registry.md). `EMBEDDING_REGISTRY` (back-compat) maps `(provider, model)` to dimension; `EMBEDDING_MODELS` in the registry carries the full metadata (dim + modalities + preview flag).

| Provider | Model | Dimensions | Modalities | API key env var |
|----------|-------|-----------|-----------|-----------------|
| `openai` | `text-embedding-3-small` | 1536 | text | `ORCHESTRATOR_OPENAI_API_KEY` |
| `openai` | `text-embedding-3-large` | 3072 | text | `ORCHESTRATOR_OPENAI_API_KEY` |
| `google` | `text-embedding-004` | 768 | text | `ORCHESTRATOR_GOOGLE_API_KEY` |
| `google` / `vertex` | `gemini-embedding-2` | 3072 (Matryoshka) | **text + image + video + audio** | GCP credentials + `ORCHESTRATOR_VERTEX_PROJECT` (or `ORCHESTRATOR_GOOGLE_API_KEY`) |
| `google` / `vertex` | `gemini-embedding-001` | 3072 | text | GCP credentials + `ORCHESTRATOR_VERTEX_PROJECT` |
| `vertex` | `text-embedding-005` | 768 | text | GCP credentials + `ORCHESTRATOR_VERTEX_PROJECT` |
| `vertex` | `text-multilingual-embedding-002` | 768 | text | GCP credentials + `ORCHESTRATOR_VERTEX_PROJECT` |

**`gemini-embedding-2` is Google's first natively multimodal embedding model** — text, images, video, and audio all map into a single 3072-dim space. For KBs ingesting mixed-media content (screenshot library, audio transcripts alongside PDFs, etc.) this is the recommended default on Vertex/Google tenants. Pick it at KB creation time and the pipeline routes attachments as native parts instead of pre-transcribing to text. Dim is reducible via `output_dimensionality` for cost-tuning.

### Task types

- **Document ingestion:** `task_type="RETRIEVAL_DOCUMENT"` — optimizes embeddings for document storage.
- **Query:** `task_type="RETRIEVAL_QUERY"` — optimizes embeddings for search queries.

Google and Vertex providers use these task types natively. OpenAI does not differentiate.

### Batching

Texts are batched according to `ORCHESTRATOR_EMBEDDING_BATCH_SIZE` (default 100). Each sub-batch makes one API call. Functions:

- `get_embeddings_batch(texts, provider, model, task_type)` — async
- `get_embeddings_batch_sync(...)` — sync wrapper for workers
- `get_embedding_sync(text, ...)` — single text, sync

---

## Chunking strategies

Defined in `backend/app/engine/chunker.py`. Called via `chunk_text(text, strategy, chunk_size, chunk_overlap, **kwargs)`.

### `recursive` (default)

Splits text on a hierarchy of separators: `\n\n`, `\n`, `. `, ` `, then character-level. Each chunk respects `chunk_size` (in characters) with `chunk_overlap` character overlap between consecutive chunks.

### `token`

Uses `tiktoken` with the `cl100k_base` encoding. `chunk_size` and `chunk_overlap` are in **token counts**. The overlap is clamped to `chunk_size - 1` to prevent infinite loops, and the step always advances by at least 1 token.

### `markdown`

Structure-aware splitting that respects Markdown headings (`#` through `######`) and fenced code blocks. Each heading creates a new section. Sections smaller than `chunk_size` become a single chunk with `heading_path` metadata. Oversized sections are re-split using the recursive strategy.

### `semantic`

Embedding-based splitting. Sentences are individually embedded, and splits occur where cosine similarity between adjacent sentence embeddings drops below `semantic_threshold` (default 0.5). Requires an `embed_fn` callback (the ingestion pipeline passes the batch embedding function). Falls back to recursive if no `embed_fn` is provided.

**Parameters for all strategies:**

| Parameter | Default | Notes |
|-----------|---------|-------|
| `chunk_size` | 1000 | Characters (recursive/markdown) or tokens (token) |
| `chunk_overlap` | 200 | Characters or tokens |
| `semantic_threshold` | 0.5 | Only for semantic strategy |

---

## Vector store backends

### Interface

Defined in `backend/app/engine/vector_store/__init__.py`.

```python
class VectorStore(ABC):
    def add_embeddings(self, kb_id, tenant_id, chunks: list[ChunkData]) -> int
    def search(self, kb_id, tenant_id, query_embedding, top_k, score_threshold) -> list[ChunkResult]
    def delete_by_document(self, kb_id, document_id) -> int
    def delete_by_kb(self, kb_id) -> None
```

**Data classes:**
- `ChunkData`: `chunk_id`, `document_id`, `content`, `chunk_index`, `embedding`, `metadata`
- `ChunkResult`: same fields plus `score`

**Factory:** `get_vector_store(backend, **kwargs)` — returns the appropriate implementation.

### pgvector (`pgvector_store.py`)

- Stores embeddings in the `kb_chunks` table using the PostgreSQL `vector` type.
- Similarity: cosine distance via the `<=>` operator. Score = `1 - (embedding <=> query)`.
- HNSW index (`vector_cosine_ops`) for approximate nearest neighbor search.
- The `score_threshold` filter is applied **server-side** in the SQL `WHERE` clause before `LIMIT`, ensuring correct top-K results.
- Tenant isolation via `tenant_id` filter and RLS.
- Requires: `pgvector/pgvector:pg16` Docker image and `CREATE EXTENSION vector`.

### FAISS (`faiss_store.py`)

- Stores indexes as local files: `{kb_id}.index` + `{kb_id}.meta.json` under `ORCHESTRATOR_FAISS_INDEX_DIR` (default `./faiss_indexes`).
- Uses `IndexFlatIP` on **L2-normalized** vectors (inner product = cosine similarity).
- Thread-safe via a lock for concurrent access.
- `tenant_id` filtering is done in Python after FAISS search.
- Delete-by-document rebuilds the index from kept rows.
- Validates embedding dimension consistency when adding to existing indexes.
- No external service dependencies — runs in-process.

### Choosing a backend

| Aspect | pgvector | FAISS |
|--------|----------|-------|
| Persistence | PostgreSQL (durable) | Local files (ephemeral in containers) |
| Scalability | Scales with PostgreSQL | Single-process, in-memory |
| Tenant isolation | SQL + RLS | Python-level filter |
| Approximate search | HNSW index | Flat (exact) search |
| Setup | Requires pgvector extension | No external dependencies |

**Recommendation:** Use `pgvector` for production deployments. Use `faiss` for development, testing, or single-tenant scenarios where simplicity is preferred.

---

## Ingestion pipeline

Orchestrated by `backend/app/engine/ingestor.py` and dispatched by `backend/app/workers/tasks.py`.

### Step-by-step flow

1. **Upload** — `POST /api/v1/knowledge-bases/{kb_id}/documents` accepts a multipart file upload. Creates a `KBDocument` record with `status: pending`. File content is base64-encoded and passed to the task.

2. **Dispatch** — `ingest_document_task` is enqueued to Celery (or run in-process if Celery is disabled). The task sets `status: processing`.

3. **Parse** — `parse_document(raw_bytes, content_type, filename)`:
   - PDF: extracted via PyMuPDF (`pymupdf`)
   - Text/Markdown/CSV/HTML: decoded as UTF-8
   - Fallback: attempts UTF-8 decode

4. **Chunk** — `chunk_text(text, strategy, chunk_size, chunk_overlap, **kwargs)`:
   - For semantic strategy, the `embed_fn` callback is injected from the batch embedding function.
   - Returns a list of `ChunkResult` objects with sequential `chunk_index` values.

5. **Embed** — `get_embeddings_batch_sync(texts, provider, model, task_type="RETRIEVAL_DOCUMENT")`:
   - Splits texts into sub-batches of `embedding_batch_size`.
   - Makes one API call per sub-batch.
   - Returns a flat list of embedding vectors.

6. **Store** — `vector_store.add_embeddings(kb_id, tenant_id, chunks)`:
   - pgvector: inserts rows into `kb_chunks`.
   - FAISS: adds vectors to the index file + metadata JSON.

7. **Finalize** — Document `status` set to `ready`, `chunk_count` updated, `kb.document_count` recalculated from all "ready" documents.

### Error handling

If any step fails, the document `status` is set to `failed` and the error message is stored in `doc.error`. The KB remains usable — only the failed document is affected.

### Supported file types

| Type | Content types | Parser |
|------|---------------|--------|
| PDF | `application/pdf` | PyMuPDF |
| Plain text | `text/plain` | UTF-8 decode |
| Markdown | `text/markdown` | UTF-8 decode |
| CSV | `text/csv` | UTF-8 decode |
| HTML | `text/html` | UTF-8 decode |

**Max file size:** `ORCHESTRATOR_KB_MAX_FILE_SIZE_MB` (default 50 MB).

---

## Retrieval pipeline

Implemented in `backend/app/engine/retriever.py`.

### Step-by-step flow

1. **Load KBs** — Fetches `KnowledgeBase` rows for the given `kb_ids` and `tenant_id`.

2. **Validate** — Checks that all selected KBs use the same `embedding_provider` and `embedding_model`. Logs a warning if mismatched (uses the first KB's model for the query).

3. **Embed query** — `get_embedding_sync(query, provider, model, task_type="RETRIEVAL_QUERY")`.

4. **Search** — Groups KBs by `vector_store` backend. For each backend, calls `vector_store.search(kb_id, tenant_id, query_embedding, top_k, score_threshold)` per KB.

5. **Merge & rank** — Combines results from all KBs, sorts by `score` descending, takes the global `top_k`.

6. **Enrich** — Joins `document_filename` from `kb_documents` for each chunk.

7. **Return** — List of dicts: `content`, `score` (4 decimal places), `chunk_index`, `document_id`, `document_filename`, `metadata`.

### Usage in workflows

The **Knowledge Retrieval** node calls `retrieve_chunks` and produces:

```json
{
  "chunks": [...],
  "context_text": "chunk1 content\n\n---\n\nchunk2 content\n\n---\n\n...",
  "query": "the resolved query",
  "chunk_count": 5
}
```

Downstream LLM Agent nodes can reference `{{ node_X.context_text }}` in their system prompt to inject retrieved context.

---

## API endpoints

See [API Reference](api-reference.md) for full details. Quick summary:

### Options (read-only)

- `GET /api/v1/knowledge-bases/embedding-options` — available providers/models with dimensions
- `GET /api/v1/knowledge-bases/chunking-strategies` — available strategies with descriptions
- `GET /api/v1/knowledge-bases/vector-stores` — available backends with descriptions

### Knowledge base CRUD

- `POST /api/v1/knowledge-bases` — create (specify embedding, chunking, vector store config)
- `GET /api/v1/knowledge-bases` — list all
- `GET /api/v1/knowledge-bases/{kb_id}` — get one
- `PUT /api/v1/knowledge-bases/{kb_id}` — update name/description
- `DELETE /api/v1/knowledge-bases/{kb_id}` — delete KB and all vectors

### Document management

- `POST /api/v1/knowledge-bases/{kb_id}/documents` — upload file (async ingestion)
- `GET /api/v1/knowledge-bases/{kb_id}/documents` — list documents with status
- `DELETE /api/v1/knowledge-bases/{kb_id}/documents/{doc_id}` — delete document and chunks

### Search

- `POST /api/v1/knowledge-bases/{kb_id}/search` — vector similarity search

---

## Configuration reference

All settings use the `ORCHESTRATOR_` prefix.

| Env var | Default | Description |
|---------|---------|-------------|
| `ORCHESTRATOR_EMBEDDING_DEFAULT_PROVIDER` | `openai` | Default embedding provider for new KBs |
| `ORCHESTRATOR_EMBEDDING_DEFAULT_MODEL` | `text-embedding-3-small` | Default embedding model |
| `ORCHESTRATOR_EMBEDDING_BATCH_SIZE` | `100` | Max texts per API call |
| `ORCHESTRATOR_KB_MAX_FILE_SIZE_MB` | `50` | Upload size limit |
| `ORCHESTRATOR_KB_DEFAULT_VECTOR_STORE` | `pgvector` | Default vector store backend |
| `ORCHESTRATOR_KB_DEFAULT_CHUNKING_STRATEGY` | `recursive` | Default chunking strategy |
| `ORCHESTRATOR_FAISS_INDEX_DIR` | `./faiss_indexes` | Directory for FAISS index files |
| `ORCHESTRATOR_VERTEX_PROJECT` | `""` | Google Cloud project for Vertex AI |
| `ORCHESTRATOR_VERTEX_LOCATION` | `us-central1` | Google Cloud region for Vertex AI |

### API keys for embedding providers

| Provider | Env var |
|----------|---------|
| OpenAI | `ORCHESTRATOR_OPENAI_API_KEY` |
| Google GenAI | `ORCHESTRATOR_GOOGLE_API_KEY` |
| Vertex AI | Application Default Credentials + `ORCHESTRATOR_VERTEX_PROJECT` |

---

## File map

| File | Purpose |
|------|---------|
| `backend/app/engine/embedding_provider.py` | Embedding registry, batch embedding, sync/async wrappers |
| `backend/app/engine/chunker.py` | Four chunking strategies |
| `backend/app/engine/ingestor.py` | Document parsing + ingestion orchestration |
| `backend/app/engine/retriever.py` | Query embedding + multi-KB vector search + ranking |
| `backend/app/engine/vector_store/__init__.py` | Abstract `VectorStore` base class + factory |
| `backend/app/engine/vector_store/pgvector_store.py` | PostgreSQL pgvector implementation |
| `backend/app/engine/vector_store/faiss_store.py` | FAISS in-memory/file implementation |
| `backend/app/api/knowledge.py` | REST API router |
| `backend/app/models/knowledge.py` | SQLAlchemy models (`KnowledgeBase`, `KBDocument`, `KBChunk`) |
| `backend/app/workers/tasks.py` | `ingest_document_task` Celery task |
| `backend/alembic/versions/0009_add_knowledge_base_tables.py` | Migration: tables, pgvector extension, HNSW index, RLS |
| `frontend/src/components/toolbar/KnowledgeBaseDialog.tsx` | KB management UI |
| `frontend/src/components/sidebar/KBMultiSelect.tsx` | KB selector for node config |
