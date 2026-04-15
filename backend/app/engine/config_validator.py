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

    return warnings


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
