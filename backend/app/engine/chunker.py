"""Text chunking strategies for RAG document ingestion.

Four strategies, selectable per knowledge base:
  - **recursive**  — recursive character splitting (default, best for 80% of cases)
  - **token**      — tiktoken-based token-count splitting
  - **markdown**   — structure-aware splitting on headings / code fences
  - **semantic**   — embedding-based splitting at topic-shift boundaries
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    content: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, Callable[..., list[ChunkResult]]] = {}


def chunk_text(
    text: str,
    strategy: str,
    chunk_size: int,
    chunk_overlap: int,
    **kwargs: Any,
) -> list[ChunkResult]:
    """Split *text* using the named strategy and return ordered chunks."""
    fn = STRATEGY_REGISTRY.get(strategy)
    if fn is None:
        raise ValueError(
            f"Unknown chunking strategy: {strategy!r}. "
            f"Available: {list(STRATEGY_REGISTRY.keys())}"
        )
    chunks = fn(text, chunk_size, chunk_overlap, **kwargs)
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


# ---------------------------------------------------------------------------
# 1. Recursive character splitting
# ---------------------------------------------------------------------------

_RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _chunk_recursive(
    text: str, chunk_size: int, chunk_overlap: int, **_kwargs: Any
) -> list[ChunkResult]:
    pieces = _split_recursive(text, chunk_size, _RECURSIVE_SEPARATORS)
    return _merge_with_overlap(pieces, chunk_size, chunk_overlap)


def _split_recursive(
    text: str, chunk_size: int, separators: list[str]
) -> list[str]:
    if len(text) <= chunk_size or not separators:
        return [text] if text.strip() else []

    sep = separators[0]
    rest_seps = separators[1:]

    if sep == "":
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    parts = text.split(sep)
    results: list[str] = []
    current = ""

    for part in parts:
        candidate = f"{current}{sep}{part}" if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                results.append(current)
            if len(part) > chunk_size:
                results.extend(_split_recursive(part, chunk_size, rest_seps))
                current = ""
            else:
                current = part

    if current.strip():
        results.append(current)
    return results


def _merge_with_overlap(
    pieces: list[str], chunk_size: int, overlap: int
) -> list[ChunkResult]:
    if not pieces:
        return []

    chunks: list[ChunkResult] = []
    for piece in pieces:
        if chunks and overlap > 0:
            prev = chunks[-1].content
            overlap_text = prev[-overlap:] if len(prev) > overlap else prev
            merged = overlap_text + piece
            if len(merged) <= chunk_size:
                chunks.append(ChunkResult(content=merged, chunk_index=0))
                continue
        chunks.append(ChunkResult(content=piece, chunk_index=0))
    return chunks


STRATEGY_REGISTRY["recursive"] = _chunk_recursive


# ---------------------------------------------------------------------------
# 2. Token-based splitting (tiktoken)
# ---------------------------------------------------------------------------

def _chunk_token(
    text: str, chunk_size: int, chunk_overlap: int, **_kwargs: Any
) -> list[ChunkResult]:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)

    if len(tokens) <= chunk_size:
        return [ChunkResult(content=text, chunk_index=0)]

    overlap = min(chunk_overlap, chunk_size - 1)  # prevent zero-progress loop

    chunks: list[ChunkResult] = []
    start = 0
    while start < len(tokens):
        end = start + chunk_size
        chunk_tokens = tokens[start:end]
        chunk_text_str = enc.decode(chunk_tokens)
        chunks.append(ChunkResult(content=chunk_text_str, chunk_index=0))
        step = chunk_size - overlap
        start += max(step, 1)

    return chunks


STRATEGY_REGISTRY["token"] = _chunk_token


# ---------------------------------------------------------------------------
# 3. Markdown-aware splitting
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def _chunk_markdown(
    text: str, chunk_size: int, chunk_overlap: int, **_kwargs: Any
) -> list[ChunkResult]:
    sections = _split_markdown_sections(text)
    chunks: list[ChunkResult] = []

    for heading_path, section_text in sections:
        if len(section_text) <= chunk_size:
            chunks.append(
                ChunkResult(
                    content=section_text.strip(),
                    chunk_index=0,
                    metadata={"heading_path": heading_path},
                )
            )
        else:
            sub_chunks = _chunk_recursive(section_text, chunk_size, chunk_overlap)
            for sc in sub_chunks:
                sc.metadata["heading_path"] = heading_path
            chunks.extend(sub_chunks)

    return [c for c in chunks if c.content.strip()]


def _split_markdown_sections(text: str) -> list[tuple[list[str], str]]:
    """Split markdown into (heading_path, section_body) tuples."""
    lines = text.split("\n")
    sections: list[tuple[list[str], str]] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []

    def flush():
        body = "\n".join(current_lines)
        if body.strip():
            path = [h[1] for h in heading_stack]
            sections.append((list(path), body))
        current_lines.clear()

    in_code_block = False
    for line in lines:
        if _CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            current_lines.append(line)
            continue

        if in_code_block:
            current_lines.append(line)
            continue

        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            current_lines.append(line)
        else:
            current_lines.append(line)

    flush()

    if not sections:
        sections.append(([], text))

    return sections


STRATEGY_REGISTRY["markdown"] = _chunk_markdown


# ---------------------------------------------------------------------------
# 4. Semantic splitting (embedding-based)
# ---------------------------------------------------------------------------

def _chunk_semantic(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    *,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    semantic_threshold: float = 0.5,
    **_kwargs: Any,
) -> list[ChunkResult]:
    if embed_fn is None:
        logger.warning("Semantic chunking: no embed_fn provided, falling back to recursive")
        return _chunk_recursive(text, chunk_size, chunk_overlap)

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [ChunkResult(content=text, chunk_index=0)]

    embeddings = embed_fn(sentences)

    groups: list[list[str]] = [[sentences[0]]]
    for i in range(1, len(sentences)):
        sim = _cosine_sim(embeddings[i - 1], embeddings[i])
        if sim < semantic_threshold:
            groups.append([sentences[i]])
        else:
            groups[-1].append(sentences[i])

    chunks: list[ChunkResult] = []
    for group in groups:
        group_text = " ".join(group)
        if len(group_text) > chunk_size:
            sub = _chunk_recursive(group_text, chunk_size, chunk_overlap)
            chunks.extend(sub)
        elif group_text.strip():
            chunks.append(ChunkResult(content=group_text, chunk_index=0))

    return chunks


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in parts if s.strip()]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


STRATEGY_REGISTRY["semantic"] = _chunk_semantic
