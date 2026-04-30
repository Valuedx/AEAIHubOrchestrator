"""CTX-MGMT.A — per-node output budget + overflow artifacts.

Pure helpers for detecting oversized node outputs, materialising a
small in-context stub that preserves the keys downstream Jinja
templates are likely to need, and persisting the full output to the
``node_output_artifacts`` side-channel.

Why this exists
---------------

Without a per-node budget, a single ReAct node's ``iterations`` list
(easily 50 kB), an HTTP node returning a 500 kB JSON response, or a
ForEach over 100 items can balloon the in-memory ``context`` and the
persisted ``instance.context_json`` arbitrarily. Each
``InstanceCheckpoint`` row writes the *entire* clean context, so a
28-node graph compounds 28× the storage cost. LangChain's *State of
Agent Engineering 2026* identifies exactly this class as the #1
production-failure category in agent systems. Anthropic's effective-
context-engineering guidance: *"keep state lean and store large data
externally with references"*.

Public surface
--------------

  * ``DEFAULT_OUTPUT_BUDGET_BYTES`` — the engine-level default
    (64 kB). Per-node override via ``data.config.contextOutputBudget``.
  * ``estimate_output_size(output)`` — fast size approximation in
    bytes (JSON encode length, default-str fallback for non-JSON
    values like UUIDs, datetimes).
  * ``should_overflow(output, budget)`` — bool predicate.
  * ``materialize_overflow_stub(output, budget, artifact_id)`` —
    construct the small in-context replacement dict. Preserves
    top-level scalar keys (id, status, error, etc.) so common
    downstream Jinja patterns (`{{ node_X.status }}`) still resolve.
  * ``persist_artifact(db, ...)`` — INSERT one row into
    ``node_output_artifacts`` and return the new uuid.

Design rules
------------

  * Pure where it can be — `should_overflow`, `materialize_*`, and
    `estimate_output_size` take primitives in and return primitives
    out, no DB. `persist_artifact` is the one DB-touching helper.
  * Top-level scalar preservation — the stub keeps `id`, `status`,
    `state`, `error`, `branch`, `category`, `result`, plus any
    other top-level scalar value that fits in 256 chars. This is
    what the legacy V8/V9/V10 prompts and Bridge/Switch nodes
    actually reach for; full nested objects are NOT preserved
    (they're what we're trying to evict in the first place).
  * Defensive size estimation — `json.dumps(default=str)` so
    non-JSON values (UUID, datetime, bytes) don't raise; bytes-mode
    so the budget check is in actual storage units, not Python
    char-count.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any

logger = logging.getLogger(__name__)


# Engine-level default. ~16 k tokens of JSON which is plenty of
# headroom for an LLM/HTTP/MCP output to be useful inline. Matches
# Anthropic's default cache breakpoint at the time of writing so the
# "small enough to inline" threshold doesn't accidentally clash with
# the cache-control breakpoint.
DEFAULT_OUTPUT_BUDGET_BYTES = 64 * 1024  # 64 kB

# Hard ceiling regardless of per-node override. A misconfigured
# ``contextOutputBudget: 999999999`` should still get clamped — the
# in-memory cost compounds across nodes, so single-node 1 GB outputs
# should still trip the overflow path even if an author asked nicely.
HARD_CEILING_BYTES = 256 * 1024  # 256 kB

# Top-level keys we ALWAYS try to preserve in the stub when present
# and small. These are the keys downstream Jinja templates and
# Switch/Condition expressions almost universally reach for.
_PRESERVE_KEYS = (
    "id", "status", "state", "branch", "error", "category",
    "result", "code", "type", "kind", "name", "title",
    "session_id", "instance_id", "request_id", "agent_id",
    "workflow_id", "case_id",
)

# Per-preserved-value cap. A `state` that's 5 kB of XML isn't useful
# to inline — keep the SHAPE (we recorded the key) but drop the
# bloated value into the artifact.
_MAX_PRESERVED_VALUE_CHARS = 256


def estimate_output_size(output: Any) -> int:
    """Approximate the JSON-encoded size of ``output`` in bytes.

    Uses ``default=str`` so non-JSON-serialisable values (UUIDs,
    datetimes, bytes) don't raise — they get stringified. Returns 0
    for None/empty so the overflow check has a clean fast path.
    """
    if output is None:
        return 0
    try:
        return len(json.dumps(output, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        # Last-resort: repr length, very rough. Logged because hitting
        # this path means a node returned something that can't even
        # be repr'd cleanly.
        logger.warning(
            "estimate_output_size: json.dumps fallback failed (%s); "
            "using repr(): repr-len=%d",
            exc, len(repr(output)),
        )
        return len(repr(output))


def resolve_budget(node_data: dict[str, Any] | None) -> int:
    """Resolve the per-node budget from ``data.config.contextOutputBudget``,
    falling back to ``DEFAULT_OUTPUT_BUDGET_BYTES``. Always clamped to
    ``HARD_CEILING_BYTES``.

    Negative or zero values fall through to the default — we never
    treat them as "disable overflow" because that's the path of
    silent unbounded growth this whole module exists to prevent.
    """
    if not isinstance(node_data, dict):
        return DEFAULT_OUTPUT_BUDGET_BYTES
    config = node_data.get("config") or {}
    raw = config.get("contextOutputBudget")
    try:
        budget = int(raw) if raw is not None else DEFAULT_OUTPUT_BUDGET_BYTES
    except (TypeError, ValueError):
        budget = DEFAULT_OUTPUT_BUDGET_BYTES
    if budget <= 0:
        budget = DEFAULT_OUTPUT_BUDGET_BYTES
    return min(budget, HARD_CEILING_BYTES)


def should_overflow(output: Any, budget: int) -> bool:
    """True iff ``output``'s estimated size exceeds ``budget``."""
    if output is None:
        return False
    return estimate_output_size(output) > budget


def _scalar_preview(value: Any) -> Any:
    """Return ``value`` as-is if it's a small scalar; truncate strings;
    omit (return ``None``) for anything that wouldn't fit in the
    preserved-value cap. Lists and dicts are NOT scalars — preserve
    only their shape (length).
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_PRESERVED_VALUE_CHARS:
            return value
        return value[:_MAX_PRESERVED_VALUE_CHARS - 1] + "…"
    if isinstance(value, (list, tuple)):
        return f"<list[{len(value)}]>"
    if isinstance(value, dict):
        return f"<dict[{len(value)} keys]>"
    # UUIDs, datetimes, etc. — let json default=str do the lifting.
    text = str(value)
    if len(text) <= _MAX_PRESERVED_VALUE_CHARS:
        return text
    return text[:_MAX_PRESERVED_VALUE_CHARS - 1] + "…"


def materialize_overflow_stub(
    output: Any,
    *,
    budget: int,
    size_bytes: int,
    artifact_id: str,
    kind: str = "overflow",
) -> dict[str, Any]:
    """Build the in-context stub that replaces an oversized output.

    Shape (CTX-MGMT.K updated)::

        {
          "_overflow": True,           # or "_compacted": True for kind="compaction"
          "_artifact_id": "<uuid>",
          "size_bytes": N,
          "budget_bytes": M,
          "summary": "<one-line human-readable>",
          # Canonical scalar keys hoisted to top level so common Jinja
          # patterns ({{ node_X.id }}, {{ node_X.status }}) still
          # resolve identically to a non-stub output.
          "id": "...", "status": "...", "error": "...", ...
          "preview": {
              "top_level_keys": [...],
              "<scalar key>": <truncated value>,
              ...
          }
        }

    Both top-level and preview carry the same canonical scalar values
    (``id``, ``status``, ``state``, ``error``, ``branch``, ``result``,
    ``code``, ``name``, ``session_id``, ``instance_id``,
    ``request_id``, ``agent_id``, ``workflow_id``, ``case_id``) — top-
    level for backward-compatible Jinja patterns; preview for explicit
    "I know I'm reading a stub" code paths.

    ``kind`` controls the marker: ``"overflow"`` stamps ``_overflow:
    True`` (CTX-MGMT.A — output was too big when first written);
    ``"compaction"`` stamps ``_compacted: True`` (CTX-MGMT.K — output
    was inline initially and later compacted to free context space).
    Both shapes share the artifact storage and the inspect tool
    surfaces.

    For non-dict outputs (a node that returned a list or scalar),
    ``preview`` carries a ``__output_type`` field instead of
    top-level keys.
    """
    if kind == "compaction":
        marker_key = "_compacted"
        summary = (
            f"Output ({size_bytes:,} bytes) compacted from context to "
            f"free space; full payload in artifact {artifact_id}."
        )
    else:
        marker_key = "_overflow"
        summary = (
            f"Output exceeded {budget:,} byte budget ({size_bytes:,} bytes); "
            f"full payload in artifact {artifact_id}."
        )

    preview: dict[str, Any] = {}
    # Top-level canonical scalars — kept synchronised with `preview`
    # so Jinja templates render the same value either way.
    canonical: dict[str, Any] = {}
    if isinstance(output, dict):
        preview["top_level_keys"] = sorted(output.keys())[:30]
        for key in _PRESERVE_KEYS:
            if key in output:
                scalar = _scalar_preview(output[key])
                if scalar is not None:
                    preview[key] = scalar
                    canonical[key] = scalar
        # Also include any OTHER top-level scalar fields that fit
        # the preserved-value cap and aren't in _PRESERVE_KEYS — for
        # node-specific fields the engine doesn't know about.
        # Capped at 8 to keep the stub itself bounded.
        extra_count = 0
        for key, value in output.items():
            if key in preview or key in _PRESERVE_KEYS:
                continue
            if not isinstance(value, (bool, int, float, str)) or value is None:
                continue
            if isinstance(value, str) and len(value) > _MAX_PRESERVED_VALUE_CHARS:
                continue
            preview[key] = value
            canonical[key] = value
            extra_count += 1
            if extra_count >= 8:
                break
    elif isinstance(output, list):
        preview["__output_type"] = f"list[{len(output)}]"
    else:
        preview["__output_type"] = type(output).__name__
        preview["__output_repr"] = _scalar_preview(output)

    stub: dict[str, Any] = {
        marker_key: True,
        "_artifact_id": artifact_id,
        "size_bytes": size_bytes,
        "budget_bytes": budget,
        "summary": summary,
        "preview": preview,
    }
    # Top-level canonical scalars — added LAST so they don't collide
    # with the engine-reserved meta keys above.
    stub.update(canonical)
    return stub


def persist_artifact(
    db: Any,
    *,
    tenant_id: str,
    instance_id: Any,
    node_id: str,
    output: Any,
    size_bytes: int,
    budget_bytes: int,
) -> str:
    """INSERT one row into ``node_output_artifacts`` and return the
    new artifact UUID as a string.

    Caller owns the commit boundary — we ``flush()`` so the row's id
    is allocated, but defer commit to the engine's checkpoint commit
    so the artifact write is atomic with the per-node ExecutionLog
    write that records the overflow.
    """
    from app.models.workflow import NodeOutputArtifact

    artifact = NodeOutputArtifact(
        id=_uuid.uuid4(),
        tenant_id=tenant_id,
        instance_id=instance_id,
        node_id=node_id,
        output_json=output,
        size_bytes=size_bytes,
        budget_bytes=budget_bytes,
    )
    db.add(artifact)
    db.flush()
    return str(artifact.id)


def maybe_overflow(
    db: Any,
    *,
    tenant_id: str,
    instance_id: Any,
    node_id: str,
    node_data: dict[str, Any],
    output: Any,
) -> tuple[Any, dict[str, Any] | None]:
    """End-to-end overflow check for one node's output.

    Returns ``(in_context_value, overflow_metadata_or_None)``. The
    caller assigns ``in_context_value`` to ``context[node_id]``;
    ``overflow_metadata`` is a small dict suitable for the
    ExecutionLog ``output_json`` so the per-node log records that an
    overflow happened (the log's payload itself stays small).

    No-overflow path: returns ``(output, None)`` and never touches
    the DB. This keeps the hot path (small outputs) at near-zero cost.
    """
    budget = resolve_budget(node_data)
    size = estimate_output_size(output)
    if size <= budget:
        return output, None

    artifact_id = persist_artifact(
        db,
        tenant_id=tenant_id,
        instance_id=instance_id,
        node_id=node_id,
        output=output,
        size_bytes=size,
        budget_bytes=budget,
    )
    stub = materialize_overflow_stub(
        output, budget=budget, size_bytes=size, artifact_id=artifact_id,
    )
    overflow_meta = {
        "_overflow_artifact_id": artifact_id,
        "size_bytes": size,
        "budget_bytes": budget,
    }
    logger.info(
        "CTX-MGMT.A overflow: node=%s size=%d > budget=%d → artifact=%s",
        node_id, size, budget, artifact_id,
    )
    return stub, overflow_meta
