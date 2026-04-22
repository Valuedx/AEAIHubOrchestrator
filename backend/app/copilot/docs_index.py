"""COPILOT-01b.iii — file-backed docs grounding for the copilot.

A *non-vector* docs search over ``codewiki/*.md`` + a flattened view
of ``shared/node_registry.json``. The agent calls ``search_docs`` and
gets the top-k most relevant chunks with source paths; it calls
``get_node_examples`` and gets the registry entry + canonical config
shape for one node type.

Why file-backed, not RAG
------------------------

The orchestrator already ships a full RAG pipeline (pgvector + four
chunkers + multi-provider embeddings — see
`codewiki/rag-knowledge-base.md`). Using it here means a dedicated
system KB, an ingestion CLI, RLS carveouts so cross-tenant reads
work, plus a reindex-on-deploy dance. That's real infrastructure
for what is, in the end, a short list of structured markdown docs
that change on git commits.

This module takes the simpler path:

* Walk ``codewiki/*.md`` at first call + cache in-process.
* Chunk each file by heading (``##`` / ``###``) so the results match
  the mental shape readers already have.
* Flatten ``shared/node_registry.json`` into one chunk per node type
  plus one "all categories" index chunk.
* Rank by word-overlap between the lowercased query terms and each
  chunk's token set, with a small boost for title matches.

The upside is zero migration, no embedding provider config, and the
same tool surface as a vector-backed implementation — if a follow-up
wants to swap this out for the real RAG pipeline, the callers
(``runner_tools.dispatch``) don't change.

The downside is that word-overlap doesn't do synonyms. Good enough
for "how does the Intent Classifier work?" → finds
``node-types.md`` section; less good for "classifying the incoming
messages" → user's phrasing doesn't match the doc's vocabulary. The
agent is coached to rephrase via the system prompt when results
look thin.

Cache invalidation
------------------

The index is loaded once on first call and cached in
``_CACHE``. Call ``reset_cache()`` to force a reload — useful in
tests and after a local ``git pull`` that changed the docs.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.paths import CODEWIKI_DIR, SHARED_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index shape
# ---------------------------------------------------------------------------


@dataclass
class DocChunk:
    """One indexable chunk.

    * ``source_path`` is a project-relative path like
      ``codewiki/automationedge.md`` so the agent can tell the user
      exactly which file to open for more.
    * ``title`` is the most specific heading that covers this chunk
      (the ``###`` if there is one, else the ``##``, else the doc's
      first ``#``).
    * ``anchor`` is the GitHub-style slug of ``title`` so a future
      UI can deep-link.
    * ``tokens`` is a precomputed lowercased token set used for
      ranking — the search function doesn't retokenise per query.
    * ``kind`` distinguishes codewiki chunks from registry chunks so
      ``get_node_examples`` can filter efficiently.
    * ``node_type`` is populated only for registry chunks so the
      node-examples tool can look up a single type directly.
    """

    source_path: str
    title: str
    anchor: str
    text: str
    tokens: frozenset[str]
    kind: str        # "codewiki" | "registry" | "registry-index"
    node_type: str | None = None


# ---------------------------------------------------------------------------
# Tokenization + ranking
# ---------------------------------------------------------------------------


# A loose definition of "word" — keeps hyphens inside (good for
# "sub-workflow"), strips punctuation.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")


# Don't waste ranking budget on stopwords.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for",
    "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "have", "has", "had", "with", "without", "by", "as", "at",
    "if", "this", "that", "these", "those", "it", "its", "from",
    "how", "what", "when", "where", "why", "who", "which",
    "can", "should", "would", "could", "may", "might", "will",
    "you", "your", "we", "our", "they", "their",
})


def _tokenize(text: str) -> set[str]:
    """Lowercase + regex-tokenise + strip stopwords. Returns a set,
    so repeated words in the query don't double-count (a long query
    with "workflow workflow workflow" shouldn't out-rank a concise
    one that mentioned it once)."""
    return {
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS
    }


def _score_chunk(chunk: DocChunk, query_tokens: set[str]) -> float:
    """Simple overlap score with a small title boost. Higher is
    better; zero means no match."""
    if not query_tokens:
        return 0.0
    body_overlap = len(query_tokens & chunk.tokens)
    if body_overlap == 0:
        # If the title alone matches, still give a tiny score so
        # "llm agent" returns the LLM Agent section even when the
        # body doesn't repeat those words verbatim.
        title_overlap = len(query_tokens & _tokenize(chunk.title))
        return title_overlap * 0.5
    # Title boost: matches in the section heading score 2x body
    # matches. Keeps "intent classifier" → the Intent Classifier
    # section, not the one paragraph elsewhere that mentions it.
    title_overlap = len(query_tokens & _tokenize(chunk.title))
    return float(body_overlap + title_overlap)


# ---------------------------------------------------------------------------
# Markdown chunker
# ---------------------------------------------------------------------------


# Heading regex: level 2 and 3 headings are the chunk boundaries.
# Level 1 is the doc title — used for anchor context but doesn't
# create a sibling chunk.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", flags=re.MULTILINE)


def _slugify(title: str) -> str:
    """GitHub-style anchor. Drops non-word chars, lowercases, joins
    with hyphens."""
    slug = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug


def _chunk_markdown(path: str, text: str) -> list[DocChunk]:
    """Split one markdown file into chunks at heading boundaries.

    Level-2 and level-3 headings open a new chunk; the chunk body is
    everything from the heading to the next heading of the same or
    stronger level. Content before the first level-2 (intro paragraph
    under the ``#`` title) goes into a synthetic "Overview" chunk.
    """
    chunks: list[DocChunk] = []
    headings = list(_HEADING_RE.finditer(text))

    # Find doc title (first level-1 heading) for fallback title on
    # chunks that somehow have no closer heading.
    doc_title = path
    for h in headings:
        if len(h.group(1)) == 1:
            doc_title = h.group(2).strip()
            break

    # Segment text into (title, body) chunks by level-2/3 boundaries.
    boundaries = [h for h in headings if len(h.group(1)) in (2, 3)]
    if not boundaries:
        # No section headings — one chunk for the whole file.
        stripped = text.strip()
        if stripped:
            chunks.append(_make_chunk(
                path=path,
                title=doc_title,
                body=stripped,
                kind="codewiki",
            ))
        return chunks

    # Intro (before first level-2 boundary).
    first_boundary = boundaries[0].start()
    intro_body = text[:first_boundary].strip()
    # Strip the leading level-1 heading line so we don't duplicate it.
    intro_body = re.sub(r"^#\s+.+?\n", "", intro_body, count=1).strip()
    if intro_body:
        chunks.append(_make_chunk(
            path=path,
            title=f"{doc_title} — Overview",
            body=intro_body,
            kind="codewiki",
        ))

    # One chunk per level-2/3 heading.
    for i, h in enumerate(boundaries):
        title = h.group(2).strip()
        body_start = h.end()
        body_end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(text)
        body = text[body_start:body_end].strip()
        if not body:
            continue
        chunks.append(_make_chunk(
            path=path,
            title=title,
            body=body,
            kind="codewiki",
        ))

    return chunks


def _make_chunk(
    *,
    path: str,
    title: str,
    body: str,
    kind: str,
    node_type: str | None = None,
) -> DocChunk:
    """Build a DocChunk with pre-computed tokens."""
    # Cap chunk length at ~4000 chars so a huge section doesn't blow
    # the tool_result payload when returned to the LLM. Most chunks
    # are well under this; cap truncates the tail and adds a marker.
    truncated_body = body
    if len(truncated_body) > 4000:
        truncated_body = truncated_body[:4000].rstrip() + "\n\n…(truncated)"
    return DocChunk(
        source_path=path,
        title=title,
        anchor=_slugify(title),
        text=truncated_body,
        tokens=frozenset(_tokenize(truncated_body)),
        kind=kind,
        node_type=node_type,
    )


# ---------------------------------------------------------------------------
# Registry flattener
# ---------------------------------------------------------------------------


def _flatten_registry(registry: dict[str, Any]) -> list[DocChunk]:
    """One chunk per node type + one categories-index chunk."""
    chunks: list[DocChunk] = []

    # Categories index — helps the agent find types by theme.
    categories = registry.get("categories") or []
    cat_lines = []
    for c in categories:
        cat_lines.append(
            f"- `{c.get('id')}` ({c.get('label')}): {c.get('description', '')}"
        )
    if cat_lines:
        chunks.append(_make_chunk(
            path="shared/node_registry.json",
            title="Node categories (overview)",
            body="The canvas organises node types into these categories:\n\n"
                 + "\n".join(cat_lines),
            kind="registry-index",
        ))

    # One chunk per type.
    for entry in registry.get("node_types") or []:
        type_id = entry.get("type") or ""
        label = entry.get("label") or type_id
        category = entry.get("category") or ""
        description = entry.get("description") or ""

        schema = entry.get("config_schema") or {}
        field_lines = []
        for field, spec in schema.items():
            field_type = spec.get("type") or "any"
            default = spec.get("default")
            enum = spec.get("enum")
            description_inline = spec.get("description") or ""
            line = f"- `{field}` ({field_type})"
            if enum:
                line += f" — one of {enum}"
            if default is not None:
                line += f"; default `{default!r}`"
            if description_inline:
                line += f" — {description_inline}"
            field_lines.append(line)

        body = (
            f"**Registry type**: `{type_id}` · **Label**: {label} · "
            f"**Category**: `{category}`\n\n"
            f"{description}\n\n"
            "### Config schema\n"
            + ("\n".join(field_lines) if field_lines else "(no config fields)")
        )
        chunks.append(_make_chunk(
            path="shared/node_registry.json",
            title=f"Node type: {label} ({type_id})",
            body=body,
            kind="registry",
            node_type=type_id,
        ))

    return chunks


# ---------------------------------------------------------------------------
# Index load + cache
# ---------------------------------------------------------------------------


_CACHE: list[DocChunk] | None = None


def reset_cache() -> None:
    """Drop the in-memory index. Next call to ``_get_index`` reloads
    from disk. Used in tests and after a local docs edit."""
    global _CACHE
    _CACHE = None


def _get_index() -> list[DocChunk]:
    """Load + memoise. First call walks the codewiki dir and parses
    the node registry; subsequent calls return the cached list."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    chunks: list[DocChunk] = []

    # Codewiki markdown. Walk the top-level dir only — nested
    # ``codewiki/plans/**`` are planning artefacts, not canonical
    # docs, and would confuse the agent.
    if CODEWIKI_DIR.exists():
        for md_path in sorted(CODEWIKI_DIR.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover — disk read
                logger.warning(
                    "docs_index: could not read %s: %s", md_path, exc,
                )
                continue
            rel_path = f"codewiki/{md_path.name}"
            chunks.extend(_chunk_markdown(rel_path, text))
    else:
        logger.warning("docs_index: CODEWIKI_DIR missing at %s", CODEWIKI_DIR)

    # Node registry.
    registry_path = SHARED_DIR / "node_registry.json"
    if registry_path.exists():
        try:
            with registry_path.open(encoding="utf-8") as f:
                registry = json.load(f)
            chunks.extend(_flatten_registry(registry))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover
            logger.warning(
                "docs_index: could not parse node_registry.json: %s", exc,
            )

    logger.info(
        "docs_index: built %d chunks (%d codewiki + %d registry-shaped)",
        len(chunks),
        sum(1 for c in chunks if c.kind == "codewiki"),
        sum(1 for c in chunks if c.kind.startswith("registry")),
    )

    _CACHE = chunks
    return _CACHE


# ---------------------------------------------------------------------------
# Public API — the functions runner_tools.dispatch calls
# ---------------------------------------------------------------------------


def search_docs(query: str, *, top_k: int = 5) -> dict[str, Any]:
    """Word-overlap search across the system docs.

    Returns a dict shaped for the LLM's tool_result event::

        {
          "query": "...",
          "match_count": N,
          "results": [
            {"source_path": "...", "title": "...", "anchor": "...",
             "score": F, "excerpt": "..."},
            ...
          ]
        }

    The result list is capped at ``top_k`` (min 1, max 20). Each
    excerpt is the chunk's full text — no separate summary step
    since we've already capped chunk length at ~4 KB.
    """
    if not isinstance(query, str) or not query.strip():
        return {"query": query, "match_count": 0, "results": []}

    k = max(1, min(int(top_k or 5), 20))
    chunks = _get_index()
    if not chunks:
        return {"query": query, "match_count": 0, "results": []}

    q_tokens = _tokenize(query)
    scored = []
    for chunk in chunks:
        score = _score_chunk(chunk, q_tokens)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:k]

    return {
        "query": query,
        "match_count": len(scored),
        "results": [
            {
                "source_path": chunk.source_path,
                "title": chunk.title,
                "anchor": chunk.anchor,
                "score": score,
                "excerpt": chunk.text,
            }
            for score, chunk in top
        ],
    }


def get_node_examples(node_type: str) -> dict[str, Any]:
    """Return the registry entry for one node type + related codewiki
    sections found via search.

    This is tighter than a free-form ``search_docs`` call because it
    keys on the exact registry type id. Result shape::

        {
          "node_type": "...",
          "registry_entry": {
            "source_path": "shared/node_registry.json",
            "title": "...",
            "excerpt": "..."
          } | null,
          "related_sections": [
            {"source_path": "codewiki/node-types.md", "title": "...",
             "score": F, "excerpt": "..."},
            ...
          ]
        }

    ``registry_entry`` is null when the node_type isn't in the
    registry — the agent should then call ``list_node_types`` to
    pick one that exists.
    """
    chunks = _get_index()
    registry_hit = next(
        (c for c in chunks if c.kind == "registry" and c.node_type == node_type),
        None,
    )

    # Related docs — search across codewiki with the node_type as
    # query. This picks up e.g. the "## LLM Agent" section in
    # node-types.md when node_type="llm_agent".
    query_terms = node_type.replace("_", " ")
    # Also include the canonical label if we have it, so
    # "intent_classifier" finds the "Intent Classifier" section.
    if registry_hit:
        # The registry chunk's title is "Node type: LLM Agent (llm_agent)" —
        # pull the label out via the parenthesised suffix.
        match = re.match(r"^Node type: (.+?) \([a-z0-9_]+\)$", registry_hit.title)
        if match:
            query_terms = f"{query_terms} {match.group(1)}"

    related = search_docs(query_terms, top_k=3)
    # Strip the registry chunk itself from related so it isn't
    # double-reported under both fields.
    related_results = [
        r for r in related["results"]
        if r["source_path"] != "shared/node_registry.json"
    ]

    return {
        "node_type": node_type,
        "registry_entry": (
            {
                "source_path": registry_hit.source_path,
                "title": registry_hit.title,
                "excerpt": registry_hit.text,
            }
            if registry_hit else None
        ),
        "related_sections": related_results,
    }


# ---------------------------------------------------------------------------
# Diagnostics — used by tests
# ---------------------------------------------------------------------------


def index_size() -> int:
    """Total chunk count — useful for sanity asserts in tests + the
    startup-checks page if we ever surface docs-indexing as a
    health signal."""
    return len(_get_index())


def iter_chunks() -> Iterable[DocChunk]:
    """Read-only view of every chunk. Tests use this to verify that
    specific sections got indexed (e.g. the AE handoff section from
    COPILOT-01b.iii's own docs)."""
    return list(_get_index())
