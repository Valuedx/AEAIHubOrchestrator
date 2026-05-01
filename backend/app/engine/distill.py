"""CTX-MGMT.J — first-class context distillation for LLM/ReAct nodes.

Generalises the V10 ``RECENT TOOL FINDINGS`` Jinja-block pattern into
a declarative config field that any LLM/ReAct node can use::

    distillBlocks: [
      {
        "label": "RECENT TOOL FINDINGS",
        "fromPath": "node_4r.json.worknotes",
        "limit": 4,
        "project": ["text"],
        "format": "bullet"
      }
    ]

Engine resolves the dotted path into context, projects requested
fields, formats the result, and appends the rendered block to the
node's rendered system prompt. Each block becomes a labelled section::

    === RECENT TOOL FINDINGS ===
    - <item 1>
    - <item 2>
    ...

Why this exists
---------------

V10's ``WORKER_PROMPT_DYNAMIC`` carries a hand-written Jinja loop
that pulls the last 4 entries of ``node_4r.json.worknotes`` and
formats them into a labelled block. Every workflow that needs
similar distillation has to re-do this — copy-paste-adapt → drift.
J makes it a one-line config entry.

Anthropic names *"structured note-taking"* as a context-pollution
mitigation. distillBlocks is the engine surface for that pattern.

Public surface
--------------

  * ``walk_dotted_path(context, path)`` — pure resolver that walks
    ``"node_4r.json.worknotes"`` into the actual nested value.
    Returns ``None`` on any miss; never raises.
  * ``render_distill_blocks(context, blocks)`` — combine multiple
    blocks into one string ready to append to a system prompt.
    Empty result on empty input or all-empty blocks.
  * ``DistillError`` for shape errors raised by the validator.

Format handlers
---------------

Three formats supported in v1:

  * ``bullet`` (default) — one line per item, ``- ...`` prefix.
  * ``numbered`` — ``1. ...``.
  * ``json`` — pretty-printed JSON of the (projected) list.

If ``format`` is unset or unknown, defaults to ``bullet``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Cap on the number of items rendered per block. Authors set ``limit``
# explicitly; we only enforce a hard ceiling so a misconfigured
# ``limit: 10000`` doesn't blow the prompt budget.
HARD_LIMIT_CEILING = 100

# Cap on the rendered character length per item — long worknote
# entries get truncated to keep the block compact.
PER_ITEM_CHAR_CAP = 280


class DistillError(ValueError):
    """Raised by the validator on malformed distillBlocks config."""


def walk_dotted_path(context: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path ``"node_4r.json.worknotes"`` into the
    actual nested value. Returns ``None`` on any miss — never raises.

    Supports list-index access via ``"items[0].name"`` syntax (the
    same shape Jinja templates use).
    """
    if not isinstance(path, str) or not path or not isinstance(context, dict):
        return None

    # Split on '.' but treat `[N]` as part of the previous segment.
    parts: list[str] = []
    buf = ""
    for ch in path:
        if ch == ".":
            if buf:
                parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)

    current: Any = context
    for raw_part in parts:
        if current is None:
            return None
        # Handle `key[idx]` shape — strip brackets first, lookup key,
        # then index.
        if "[" in raw_part:
            key, _, rest = raw_part.partition("[")
            idx_str = rest.rstrip("]")
            if key:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return None
                if current is None:
                    return None
            try:
                idx = int(idx_str)
            except ValueError:
                return None
            if isinstance(current, (list, tuple)) and -len(current) <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(raw_part)
            else:
                return None
    return current


def _coerce_to_list(value: Any) -> list[Any]:
    """Convert any value into a list of items — what blocks render
    over. Lists pass through; dicts become ``[dict]``; scalars become
    ``[scalar]``; None becomes ``[]``."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _project_fields(item: Any, project: list[str] | None) -> Any:
    """If ``project`` is set and the item is a dict, return only the
    listed fields. Otherwise return the item unchanged.

    ``project=["text"]`` on ``{"text": "x", "ts": "y"}`` → ``"x"``
    (string when only one field).
    ``project=["text", "ts"]`` on the same → ``{"text": "x", "ts": "y"}``.
    """
    if not project or not isinstance(item, dict):
        return item
    if len(project) == 1:
        return item.get(project[0])
    return {k: item.get(k) for k in project if k in item}


def _truncate_str(s: str, cap: int = PER_ITEM_CHAR_CAP) -> str:
    if len(s) <= cap:
        return s
    return s[: cap - 1] + "…"


def _render_item(item: Any) -> str:
    """Format one item as a single line of text. Strings pass through
    truncated; everything else is JSON-encoded compactly."""
    if isinstance(item, str):
        return _truncate_str(item)
    if item is None:
        return "(null)"
    try:
        text = json.dumps(item, default=str, ensure_ascii=False)
    except Exception:
        text = str(item)
    return _truncate_str(text)


def render_one_block(context: dict[str, Any], block: dict[str, Any]) -> str:
    """Render a single distill block. Returns empty string when the
    path resolves to nothing or the resulting list is empty (so the
    caller can omit the section entirely rather than emitting a
    label with no body)."""
    label = str(block.get("label") or "").strip()
    from_path = str(block.get("fromPath") or "").strip()
    raw_limit = block.get("limit")
    project = block.get("project") if isinstance(block.get("project"), list) else None
    fmt = str(block.get("format") or "bullet").lower()

    if not from_path:
        return ""

    try:
        limit = int(raw_limit) if raw_limit is not None else 0
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        limit = 0  # 0 = no limit (use all items)
    limit = min(limit or HARD_LIMIT_CEILING, HARD_LIMIT_CEILING)

    raw_value = walk_dotted_path(context, from_path)
    items = _coerce_to_list(raw_value)
    if not items:
        return ""

    # Take the LAST N items (recency bias matches the V10 worknotes
    # pattern — most recent are most relevant).
    if limit and len(items) > limit:
        items = items[-limit:]

    projected = [_project_fields(item, project) for item in items]

    label_line = f"=== {label} ===" if label else ""
    if fmt == "json":
        try:
            body = json.dumps(projected, indent=2, default=str, ensure_ascii=False)
        except Exception:
            body = str(projected)
    elif fmt == "numbered":
        body = "\n".join(
            f"{i + 1}. {_render_item(item)}"
            for i, item in enumerate(projected)
        )
    else:
        # Default: bullet.
        body = "\n".join(f"- {_render_item(item)}" for item in projected)

    if label_line:
        return f"{label_line}\n{body}"
    return body


def render_distill_blocks(
    context: dict[str, Any],
    blocks: list[dict[str, Any]] | None,
) -> str:
    """Combine multiple distill blocks into one string suitable for
    appending to a rendered system prompt.

    Empty / missing input → empty string. Blocks that resolve to no
    data are silently skipped (no empty section).

    Hot-path no-op: when ``blocks`` is None or empty, returns ""
    immediately without touching context.
    """
    if not blocks:
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        try:
            rendered = render_one_block(context, block)
        except Exception as exc:
            # Distill rendering errors are observability concerns —
            # skip the block, log, continue.
            logger.warning(
                "render_distill_blocks: block %r raised — skipping (%s)",
                block.get("label"), exc,
            )
            continue
        if rendered:
            parts.append(rendered)
    if not parts:
        return ""
    return "\n\n".join(parts)


def validate_distill_blocks(blocks: Any) -> list[str]:
    """Shape-check ``distillBlocks`` config. Returns a list of error
    strings (empty = valid). Used by ``config_validator`` at promote
    time so authors see typos early."""
    if blocks is None:
        return []
    if not isinstance(blocks, list):
        return [f"distillBlocks must be a list, got {type(blocks).__name__}"]
    out: list[str] = []
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            out.append(f"distillBlocks[{i}] must be an object, got {type(block).__name__}")
            continue
        if not block.get("fromPath"):
            out.append(f"distillBlocks[{i}] must have a non-empty 'fromPath'")
            continue
        if not isinstance(block["fromPath"], str):
            out.append(f"distillBlocks[{i}].fromPath must be a string")
        label = block.get("label")
        if label is not None and not isinstance(label, str):
            out.append(f"distillBlocks[{i}].label must be a string when set")
        limit = block.get("limit")
        if limit is not None:
            try:
                int(limit)
            except (TypeError, ValueError):
                out.append(f"distillBlocks[{i}].limit must be an integer when set")
        project = block.get("project")
        if project is not None and not isinstance(project, list):
            out.append(f"distillBlocks[{i}].project must be a list of field names when set")
        fmt = block.get("format")
        if fmt is not None and fmt not in {"bullet", "numbered", "json"}:
            out.append(
                f"distillBlocks[{i}].format must be one of "
                f"'bullet'/'numbered'/'json', got {fmt!r}"
            )
    return out
