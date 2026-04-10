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

    return warnings


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
