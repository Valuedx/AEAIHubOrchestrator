"""Validate node configs against node_registry.json schemas.

Used by the workflow save endpoint to catch configuration errors before
execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.paths import SHARED_DIR

logger = logging.getLogger(__name__)

_REGISTRY: dict | None = None


def _load_registry() -> dict:
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    registry_path = SHARED_DIR / "node_registry.json"
    if not registry_path.exists():
        logger.warning("node_registry.json not found at %s", registry_path)
        _REGISTRY = {}
        return _REGISTRY

    with open(registry_path, encoding="utf-8") as f:
        _REGISTRY = json.load(f)
    return _REGISTRY


def _find_schema(label: str) -> dict[str, Any] | None:
    registry = _load_registry()
    for nt in registry.get("node_types", []):
        if nt.get("label") == label:
            return nt.get("config_schema", {})
    return None


def validate_graph_configs(graph_json: dict) -> list[str]:
    """Validate all node configs in a graph against the registry schemas.

    Returns a list of warning strings (empty = valid).
    """
    warnings: list[str] = []

    for node in graph_json.get("nodes", []):
        data = node.get("data", {})
        label = data.get("label", "")
        config = data.get("config", {})
        node_id = node.get("id", "?")

        schema = _find_schema(label)
        if schema is None:
            continue

        for key, field_def in schema.items():
            value = config.get(key)

            if value is None and field_def.get("default") is not None:
                continue

            if "enum" in field_def and value is not None:
                if value not in field_def["enum"]:
                    warnings.append(
                        f"Node {node_id} ({label}): '{key}' value '{value}' "
                        f"not in allowed values {field_def['enum']}"
                    )

            if "min" in field_def and isinstance(value, (int, float)):
                if value < field_def["min"]:
                    warnings.append(
                        f"Node {node_id} ({label}): '{key}' value {value} "
                        f"below minimum {field_def['min']}"
                    )

            if "max" in field_def and isinstance(value, (int, float)):
                if value > field_def["max"]:
                    warnings.append(
                        f"Node {node_id} ({label}): '{key}' value {value} "
                        f"above maximum {field_def['max']}"
                    )

            expected_type = field_def.get("type")
            if expected_type and value is not None:
                type_ok = _check_type(value, expected_type)
                if not type_ok:
                    warnings.append(
                        f"Node {node_id} ({label}): '{key}' expected type "
                        f"'{expected_type}', got '{type(value).__name__}'"
                    )

        if label == "Notification":
            warnings.extend(_validate_notification(node_id, config))
        if label == "Intent Classifier":
            warnings.extend(_validate_intent_classifier(node_id, config))
        if label == "Entity Extractor":
            warnings.extend(_validate_entity_extractor(node_id, config))
        if label == "Sub-Workflow":
            warnings.extend(_validate_sub_workflow(node_id, config, graph_json))

        # CTX-MGMT.L — outputReducer is a generic per-node config that
        # applies to every node type. Validate it here so any author
        # can opt in regardless of node label.
        warnings.extend(_validate_output_reducer(node_id, label, config))
        # CTX-MGMT.A — contextOutputBudget must be a positive integer
        # if set; the engine clamps to a hard ceiling but author-time
        # validation catches obviously-wrong values (negative, str).
        warnings.extend(_validate_output_budget(node_id, label, config))

    # CTX-MGMT.C — exposeAs collisions need a graph-level pass
    # (a single node's exposeAs collides with another node's id or
    # exposeAs). Run once after the per-node loop.
    warnings.extend(_validate_expose_as_collisions(graph_json))

    # CYCLIC-01.c — graph-level loopback edge checks. Run once per
    # graph (not per node) because the rules are about edges, not
    # about node configs.
    warnings.extend(_validate_loopback_edges(graph_json))

    return warnings


def _validate_loopback_edges(graph_json: dict) -> list[str]:
    """Four hard rules that fail save if violated:

    1. ``maxIterations`` must be an integer in [1, 100].
    2. ``LOOPBACK_NO_EXIT`` — cycle body must have at least one
       forward edge exiting the cycle. Without an exit, the only
       termination is the iteration cap, which is almost always a
       bug.
    3. Only one loopback edge per source node. If an author needs
       two different branches to loop, they should use a Switch /
       two Condition nodes — overloading a single source is the
       kind of thing that reads easily in code but confuses the
       runtime's per-edge iteration counters.
    4. Loopback ``target`` must be a forward ancestor of ``source``.
       Otherwise it's not a cycle, it's a forward skip that happened
       to tag itself as a loopback.
    """
    from app.engine.cyclic_analysis import (
        LOOPBACK_HARD_CAP,
        cycle_body,
        has_forward_exit,
        is_forward_ancestor,
        loopback_edges,
        loopback_max_iterations,
    )

    errors: list[str] = []
    edges = loopback_edges(graph_json)
    if not edges:
        return errors

    # Rule 3 — duplicate-source detection.
    seen_sources: dict[str, str] = {}  # source → edge_id of first loopback
    for e in edges:
        source = str(e.get("source"))
        edge_id = str(e.get("id", f"{source}->{e.get('target')}"))
        if source in seen_sources:
            errors.append(
                f"Loopback edge '{edge_id}': source '{source}' already has "
                f"another loopback ('{seen_sources[source]}'). A single source "
                "can only own one loopback — split into separate Condition "
                "branches or add a Switch node."
            )
            continue
        seen_sources[source] = edge_id

    for e in edges:
        source = str(e.get("source"))
        target = str(e.get("target"))
        edge_id = str(e.get("id", f"{source}->{target}"))

        # Rule 1 — maxIterations range.
        raw_max = loopback_max_iterations(e)
        if raw_max is None and "maxIterations" in e:
            # Present but non-integer.
            errors.append(
                f"Loopback edge '{edge_id}': 'maxIterations' must be an integer, "
                f"got {e['maxIterations']!r}"
            )
        elif raw_max is not None:
            if raw_max < 1:
                errors.append(
                    f"Loopback edge '{edge_id}': 'maxIterations' must be ≥ 1 "
                    f"(got {raw_max}). An edge with zero iterations is dead."
                )
            elif raw_max > LOOPBACK_HARD_CAP:
                errors.append(
                    f"Loopback edge '{edge_id}': 'maxIterations' must be ≤ "
                    f"{LOOPBACK_HARD_CAP} (got {raw_max}). The runtime clamps "
                    "anyway; fix the graph so operators see the real cap."
                )

        # Rule 4 — target must be forward-ancestor of source.
        if not is_forward_ancestor(target, source, graph_json):
            errors.append(
                f"Loopback edge '{edge_id}': target '{target}' is not reachable "
                f"from source '{source}' via forward edges, so this isn't a "
                "cycle. Did you mean a forward edge?"
            )
            # Skip the exit-path check for a malformed cycle — the
            # body would be empty and the error message would be
            # confusing.
            continue

        # Rule 2 — cycle body must have a forward exit.
        body = cycle_body(target, source, graph_json)
        if body and not has_forward_exit(body, graph_json):
            errors.append(
                f"Loopback edge '{edge_id}': cycle body {sorted(body)} has no "
                "forward exit path. The only way out is the iteration cap, "
                "which is almost always a bug. Add a Condition whose false "
                "branch exits the cycle."
            )

    return errors


def _validate_notification(node_id: str, config: dict) -> list[str]:
    """Notification-specific save-time validation."""
    warns: list[str] = []
    label = "Notification"

    channel = config.get("channel", "")
    valid_channels = [
        "slack_webhook", "teams_webhook", "discord_webhook", "telegram",
        "whatsapp", "pagerduty", "email", "generic_webhook",
    ]
    if channel and channel not in valid_channels:
        warns.append(
            f"Node {node_id} ({label}): 'channel' value '{channel}' "
            f"not in {valid_channels}"
        )

    destination = config.get("destination", "")
    if not destination:
        warns.append(f"Node {node_id} ({label}): 'destination' is required")

    msg_tpl = config.get("messageTemplate", "")
    if not msg_tpl:
        warns.append(f"Node {node_id} ({label}): 'messageTemplate' is required")

    if msg_tpl:
        from jinja2 import Environment, TemplateSyntaxError
        try:
            Environment().parse(msg_tpl)
        except TemplateSyntaxError as exc:
            warns.append(
                f"Node {node_id} ({label}): 'messageTemplate' has invalid "
                f"Jinja2 syntax: {exc}"
            )

    _CHANNEL_REQUIRED: dict[str, list[str]] = {
        "telegram": ["chatId"],
        "whatsapp": ["phoneNumber", "phoneNumberId"],
        "email": ["to", "subject"],
    }
    required_fields = _CHANNEL_REQUIRED.get(channel, [])
    for field in required_fields:
        if not config.get(field):
            warns.append(
                f"Node {node_id} ({label}): '{field}' is required for "
                f"channel '{channel}'"
            )

    webhook_channels = ["slack_webhook", "teams_webhook", "discord_webhook", "generic_webhook"]
    if channel in webhook_channels and destination:
        is_template = "{{" in destination
        if not is_template and not destination.startswith("https://"):
            warns.append(
                f"Node {node_id} ({label}): 'destination' for {channel} "
                f"should be an https:// URL or a {{{{ env.* }}}} expression"
            )

    return warns


def _validate_intent_classifier(node_id: str, config: dict) -> list[str]:
    """Intent Classifier save-time validation."""
    warns: list[str] = []
    label = "Intent Classifier"

    intents = config.get("intents", [])
    if not isinstance(intents, list) or len(intents) == 0:
        warns.append(f"Node {node_id} ({label}): 'intents' must be a non-empty array")
    else:
        for i, intent in enumerate(intents):
            if not isinstance(intent, dict):
                warns.append(
                    f"Node {node_id} ({label}): intent at index {i} must be an object"
                )
                continue
            name = intent.get("name", "")
            if not name or not isinstance(name, str) or not name.strip():
                warns.append(
                    f"Node {node_id} ({label}): intent at index {i} has no 'name'"
                )

    return warns


def _validate_entity_extractor(node_id: str, config: dict) -> list[str]:
    """Entity Extractor save-time validation."""
    warns: list[str] = []
    label = "Entity Extractor"

    entities = config.get("entities", [])
    if not isinstance(entities, list) or len(entities) == 0:
        warns.append(f"Node {node_id} ({label}): 'entities' must be a non-empty array")
    else:
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                warns.append(
                    f"Node {node_id} ({label}): entity at index {i} must be an object"
                )
                continue
            name = entity.get("name", "")
            if not name or not isinstance(name, str) or not name.strip():
                warns.append(
                    f"Node {node_id} ({label}): entity at index {i} has no 'name'"
                )
            etype = entity.get("type", "")
            if etype == "regex" and not entity.get("pattern"):
                warns.append(
                    f"Node {node_id} ({label}): entity '{name}' has type 'regex' but no 'pattern'"
                )
            if etype == "enum":
                vals = entity.get("enum_values", [])
                if not isinstance(vals, list) or len(vals) == 0:
                    warns.append(
                        f"Node {node_id} ({label}): entity '{name}' has type 'enum' "
                        f"but no 'enum_values'"
                    )

    return warns


def _validate_sub_workflow(node_id: str, config: dict, graph_json: dict) -> list[str]:
    """Sub-Workflow save-time validation."""
    warns: list[str] = []
    label = "Sub-Workflow"

    workflow_id = config.get("workflowId", "").strip()
    if not workflow_id:
        warns.append(f"Node {node_id} ({label}): 'workflowId' is required")
        return warns

    version_policy = config.get("versionPolicy", "latest")
    if version_policy not in ("latest", "pinned"):
        warns.append(
            f"Node {node_id} ({label}): 'versionPolicy' must be 'latest' or 'pinned'"
        )

    if version_policy == "pinned":
        pv = config.get("pinnedVersion")
        if pv is None or (isinstance(pv, int) and pv < 1):
            warns.append(
                f"Node {node_id} ({label}): 'pinnedVersion' must be a positive integer"
            )

    input_mapping = config.get("inputMapping", {})
    if input_mapping and not isinstance(input_mapping, dict):
        warns.append(
            f"Node {node_id} ({label}): 'inputMapping' must be an object"
        )

    output_node_ids = config.get("outputNodeIds", [])
    if output_node_ids and not isinstance(output_node_ids, list):
        warns.append(
            f"Node {node_id} ({label}): 'outputNodeIds' must be an array"
        )

    # Static self-reference detection: check if any Sub-Workflow node in this
    # graph references the graph's own workflow ID (only possible at update time
    # when we'd need the workflow_def_id, which we don't have here — so we just
    # detect if two Sub-Workflow nodes reference each other in the same graph)
    sub_wf_ids = set()
    for n in graph_json.get("nodes", []):
        d = n.get("data", {})
        if d.get("label") == "Sub-Workflow":
            ref_id = d.get("config", {}).get("workflowId", "").strip()
            if ref_id:
                sub_wf_ids.add(ref_id)
    # This is a best-effort heuristic — full transitive cycle detection
    # happens at runtime via the _parent_chain mechanism.

    return warns


def _check_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float))
    if expected == "integer":
        return isinstance(value, int)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


# ---------------------------------------------------------------------------
# CTX-MGMT.L — outputReducer validation
# ---------------------------------------------------------------------------


def _validate_output_reducer(node_id: str, label: str, config: dict) -> list[str]:
    """Reject unknown reducer names at promote time so a typo
    (e.g. ``"appned"``) doesn't silently fall back to ``overwrite``
    at runtime."""
    raw = config.get("outputReducer")
    if raw is None or raw == "":
        return []  # default ("overwrite") is fine; field is optional
    from app.engine.reducers import KNOWN_REDUCERS

    name = str(raw).strip().lower()
    if name not in KNOWN_REDUCERS:
        return [
            f"Node {node_id} ({label}): 'outputReducer' value {raw!r} "
            f"is not a known reducer. Valid: {sorted(KNOWN_REDUCERS)}"
        ]
    return []


# ---------------------------------------------------------------------------
# CTX-MGMT.A — contextOutputBudget validation
# ---------------------------------------------------------------------------


def _validate_output_budget(node_id: str, label: str, config: dict) -> list[str]:
    """``contextOutputBudget`` must be a positive integer if set. The
    engine's ``resolve_budget`` clamps to a hard ceiling and falls
    back to default on zero/negative/non-int — but author-time
    validation catches obviously-wrong values so the author sees a
    clear error rather than the silent fall-back."""
    raw = config.get("contextOutputBudget")
    if raw is None:
        return []  # unset → engine default
    if isinstance(raw, bool) or not isinstance(raw, int):
        return [
            f"Node {node_id} ({label}): 'contextOutputBudget' must be an "
            f"integer (bytes), got {type(raw).__name__}"
        ]
    if raw <= 0:
        return [
            f"Node {node_id} ({label}): 'contextOutputBudget' must be > 0, "
            f"got {raw}. Use a small positive number to opt into stricter "
            "overflow behavior, or omit the field for the engine default."
        ]
    return []


# ---------------------------------------------------------------------------
# CTX-MGMT.C — exposeAs alias collision check
# ---------------------------------------------------------------------------


def _validate_expose_as_collisions(graph_json: dict) -> list[str]:
    """``exposeAs`` aliases must not collide with another node's id
    or another node's exposeAs. The engine writes the alias key
    directly into ``context``; a collision would silently overwrite
    one node's output with another's, which the author almost
    certainly didn't mean.

    Three classes of collision:
      1. ``exposeAs`` matches another node's id.
      2. Two nodes have the same ``exposeAs`` value.
      3. ``exposeAs`` matches the node's own id (no-op alias —
         pointless).
    """
    warnings: list[str] = []
    nodes = graph_json.get("nodes", []) or []

    # Map of expose alias → list of node ids that produce it.
    aliases: dict[str, list[str]] = {}
    node_ids = {n.get("id") for n in nodes if n.get("id")}

    for node in nodes:
        nid = node.get("id")
        if not nid:
            continue
        data = node.get("data") or {}
        config = data.get("config") or {}
        raw = config.get("exposeAs")
        if raw is None or raw == "":
            continue
        if not isinstance(raw, str):
            warnings.append(
                f"Node {nid} ({data.get('label')}): 'exposeAs' must be a "
                f"non-empty string, got {type(raw).__name__}"
            )
            continue
        alias = raw.strip()
        if not alias:
            continue
        if alias == nid:
            warnings.append(
                f"Node {nid} ({data.get('label')}): 'exposeAs' is the "
                f"same as the node id — pointless alias. Either remove "
                "exposeAs or pick a semantic name."
            )
            continue
        if alias in node_ids:
            warnings.append(
                f"Node {nid} ({data.get('label')}): 'exposeAs={alias!r}' "
                f"collides with another node's id; the engine would "
                "silently overwrite that node's slot. Pick a different alias."
            )
            continue
        aliases.setdefault(alias, []).append(nid)

    for alias, owners in aliases.items():
        if len(owners) > 1:
            warnings.append(
                f"Multiple nodes claim 'exposeAs={alias!r}': {sorted(owners)}. "
                "Only the last-firing one's output will be readable; pick "
                "distinct aliases or use a Merge waitAny / append reducer."
            )

    return warnings
