"""COPILOT-V2 — structured diff between two workflow graphs.

A pure helper. Two ``graph_json`` dicts in, a structured diff dict out.
Used by the ``diff_drafts`` runner tool to give the agent and the user
a "what changed" view without scrolling chat history.

Diff shape:

::

    {
      "left_label":  "current draft (v3)",
      "right_label": "base workflow X (v5)",
      "node_changes": {
        "added":    [{id, label, display_name}],
        "removed":  [{id, label, display_name}],
        "modified": [{id, label, display_name, changed_fields: [
                       {key, before_preview, after_preview, kind}, ...
                     ]}],
      },
      "edge_changes": {
        "added":    [{id, source, target, source_handle?, target_handle?}],
        "removed":  [{id, source, target, source_handle?, target_handle?}],
      },
      "summary": "3 nodes added, 1 removed, 2 modified; 4 edges added, 2 removed",
    }

What we DO compare per node:

  * ``data.label`` (registry node-type label)
  * ``data.displayName``
  * ``data.config`` — every key, recursively. Long string values get
    a length+head/tail preview rather than full dump.

What we DON'T compare:

  * ``position`` — moving a node on the canvas isn't a semantic change.
    Filtering this out keeps the diff focused on what affects
    execution.
  * ``data.status`` — runtime UI hint; not authored by anyone.
  * ``data.pinnedOutput`` — ephemeral debug data.
"""

from __future__ import annotations

from typing import Any


# Strings longer than this get a head…tail preview instead of the full
# value. 240 chars is enough to read the leading sentence of a typical
# prompt change without flooding the chat.
_PREVIEW_HEAD = 120
_PREVIEW_TAIL = 60
_PREVIEW_THRESHOLD = _PREVIEW_HEAD + _PREVIEW_TAIL + 8


def _preview(value: Any, *, max_chars: int = 240) -> Any:
    """Truncate long string values for the diff preview. Non-strings
    are returned as-is — the agent / UI can render them directly."""
    if not isinstance(value, str):
        return value
    if len(value) <= _PREVIEW_THRESHOLD:
        return value
    head = value[:_PREVIEW_HEAD]
    tail = value[-_PREVIEW_TAIL:]
    return f"{head}…[{len(value) - _PREVIEW_HEAD - _PREVIEW_TAIL} chars elided]…{tail}"


def _node_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n.get("id"): n for n in (graph.get("nodes") or []) if n.get("id")}


def _edge_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index edges by id when present, else by (source, target,
    source_handle, target_handle) so re-creating an edge with a fresh
    auto-generated id doesn't surface as add+remove."""
    out: dict[str, dict[str, Any]] = {}
    for e in graph.get("edges") or []:
        eid = e.get("id")
        if eid:
            out[str(eid)] = e
        else:
            key = (
                f"{e.get('source')}->{e.get('target')}"
                f"::{e.get('sourceHandle') or ''}/{e.get('targetHandle') or ''}"
            )
            out[key] = e
    return out


def _edge_summary(edge: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": edge.get("id") or "",
        "source": edge.get("source"),
        "target": edge.get("target"),
    }
    if edge.get("sourceHandle"):
        out["source_handle"] = edge["sourceHandle"]
    if edge.get("targetHandle"):
        out["target_handle"] = edge["targetHandle"]
    return out


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data") or {}
    return {
        "id": node.get("id"),
        "label": data.get("label"),
        "display_name": data.get("displayName"),
    }


def _diff_config_value(key_path: str, before: Any, after: Any) -> list[dict[str, Any]]:
    """Recursively diff two values, returning a flat list of
    ``{key, before_preview, after_preview, kind}`` entries. ``kind``
    is one of ``added`` / ``removed`` / ``changed`` / ``size``."""
    if before == after:
        return []

    # Both dicts → recurse per key.
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[dict[str, Any]] = []
        for k in sorted(set(before.keys()) | set(after.keys())):
            sub_path = f"{key_path}.{k}" if key_path else k
            if k not in before:
                out.append({
                    "key": sub_path,
                    "kind": "added",
                    "before_preview": None,
                    "after_preview": _preview(after[k]),
                })
            elif k not in after:
                out.append({
                    "key": sub_path,
                    "kind": "removed",
                    "before_preview": _preview(before[k]),
                    "after_preview": None,
                })
            else:
                out.extend(_diff_config_value(sub_path, before[k], after[k]))
        return out

    # Both lists → compare positionally with a length hint when the
    # length differs. Detailed item-level diff would balloon the
    # output; agent + user can request a full dump if they want it.
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [{
                "key": key_path,
                "kind": "size",
                "before_preview": f"list[{len(before)}]",
                "after_preview": f"list[{len(after)}]",
            }]
        # Same length, different content — surface as a single change.
        return [{
            "key": key_path,
            "kind": "changed",
            "before_preview": _preview(repr(before)[:240]),
            "after_preview": _preview(repr(after)[:240]),
        }]

    # Scalar / string change — surface with previews.
    return [{
        "key": key_path,
        "kind": "changed",
        "before_preview": _preview(before),
        "after_preview": _preview(after),
    }]


def _diff_node(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    """Return changed-fields entries for one node id present on both
    sides. Compares label, displayName, and every config key."""
    left_data = left.get("data") or {}
    right_data = right.get("data") or {}

    changes: list[dict[str, Any]] = []
    for top_key in ("label", "displayName"):
        before, after = left_data.get(top_key), right_data.get(top_key)
        if before != after:
            changes.append({
                "key": top_key,
                "kind": "changed",
                "before_preview": _preview(before),
                "after_preview": _preview(after),
            })

    left_cfg = left_data.get("config") or {}
    right_cfg = right_data.get("config") or {}
    changes.extend(_diff_config_value("config", left_cfg, right_cfg))
    return changes


def diff_graphs(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> dict[str, Any]:
    """Compute a structured diff between two graphs.

    ``left`` is "the current draft" by convention; ``right`` is the
    thing being compared against (another draft or the base workflow's
    published graph). The returned diff describes what would have to
    change in ``right`` to reach ``left`` — i.e. the user's edits.
    """
    left_nodes = _node_index(left)
    right_nodes = _node_index(right)
    left_edges = _edge_index(left)
    right_edges = _edge_index(right)

    added_nodes: list[dict[str, Any]] = []
    removed_nodes: list[dict[str, Any]] = []
    modified_nodes: list[dict[str, Any]] = []

    for nid, node in left_nodes.items():
        if nid not in right_nodes:
            added_nodes.append(_node_summary(node))
            continue
        changes = _diff_node(right_nodes[nid], node)
        if changes:
            entry = _node_summary(node)
            entry["changed_fields"] = changes
            modified_nodes.append(entry)

    for nid, node in right_nodes.items():
        if nid not in left_nodes:
            removed_nodes.append(_node_summary(node))

    added_edges = [
        _edge_summary(e) for k, e in left_edges.items() if k not in right_edges
    ]
    removed_edges = [
        _edge_summary(e) for k, e in right_edges.items() if k not in left_edges
    ]

    summary_parts: list[str] = []
    if added_nodes:
        summary_parts.append(f"{len(added_nodes)} node{'s' if len(added_nodes) != 1 else ''} added")
    if removed_nodes:
        summary_parts.append(f"{len(removed_nodes)} removed")
    if modified_nodes:
        summary_parts.append(f"{len(modified_nodes)} modified")
    if added_edges:
        summary_parts.append(f"{len(added_edges)} edge{'s' if len(added_edges) != 1 else ''} added")
    if removed_edges:
        summary_parts.append(f"{len(removed_edges)} removed")
    summary = "; ".join(summary_parts) if summary_parts else "no changes"

    return {
        "left_label": left_label,
        "right_label": right_label,
        "node_changes": {
            "added": added_nodes,
            "removed": removed_nodes,
            "modified": modified_nodes,
        },
        "edge_changes": {
            "added": added_edges,
            "removed": removed_edges,
        },
        "summary": summary,
    }
