"""CTX-MGMT.I — per-node output schema validation.

When a node's config declares ``data.config.outputSchema`` (a JSON
Schema dict), the engine validates the handler's output against it
at write-time. Non-fatal by default — failures stamp metadata on
the in-context value and emit a warning log; the run continues so
authors can opt into validation incrementally without breaking
existing graphs.

Why this exists
---------------

Today every node's output shape is implicit. A typo in a Jinja
template (`{{ node_4r.foo.bar }}` when the handler produces
`json.id`) renders to empty string under `_PermissiveUndefined`.
CTX-MGMT.B catches references to non-existent NODE ids; this slice
catches references to non-existent FIELDS within a declared schema.

Public surface
--------------

  * ``validate_node_output(output, schema)`` — pure validator.
    Returns ``(is_valid, errors)`` where ``errors`` is a list of
    human-readable validation strings.
  * ``schema_paths(schema)`` — returns the set of dotted attribute
    paths reachable through a JSON Schema's ``properties``. Used by
    the copilot lint to verify Jinja refs match the schema.
  * ``annotate_output_with_validation(output, schema_result)`` —
    stamps validation metadata on the in-context value when the
    handler's output is a dict; leaves non-dict outputs alone.

Design rules
------------

  * **Soft validation by default.** A schema mismatch logs a warning
    + stamps the output with ``_schema_mismatch: True`` + the list
    of errors. The handler's output is otherwise unchanged.
    Authors opt into strict mode (raises) via
    ``data.config.outputSchemaStrict: true``.
  * **Empty schema = skip.** ``outputSchema: {}`` or absent → the
    validator returns ``(True, [])`` without doing work. Hot-path
    overhead is one dict lookup.
  * **Schema rendering errors don't crash the run.** If the schema
    itself is malformed (e.g. invalid JSON Schema), we log a
    warning and skip validation for that node. Validation is
    observability, not correctness.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_node_output(
    output: Any,
    schema: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """Validate ``output`` against the JSON Schema ``schema``.

    Returns ``(is_valid, errors)``. Empty/missing schema returns
    ``(True, [])`` (no-op). Malformed schema is treated as soft
    failure: returns ``(True, ["..."])`` with the error string so
    the caller can log it but the run still continues.
    """
    if not schema:
        return True, []
    if not isinstance(schema, dict):
        return True, [f"outputSchema must be a dict, got {type(schema).__name__}"]

    try:
        from jsonschema import Draft202012Validator, ValidationError  # noqa: F401
    except ImportError:
        # jsonschema missing — soft skip with a log message. Don't
        # crash the run.
        logger.warning(
            "validate_node_output: jsonschema package not available; "
            "skipping validation"
        )
        return True, []

    try:
        validator = Draft202012Validator(schema)
    except Exception as exc:
        return True, [f"outputSchema is not a valid JSON Schema: {exc}"]

    errors: list[str] = []
    try:
        iter_errors_iter = list(validator.iter_errors(output))
    except Exception as exc:
        # Schema construction was lenient but iter_errors found
        # a structural problem (`type: 12345` etc.) — treat as
        # malformed-schema soft fail.
        return True, [f"outputSchema raised during validation: {exc}"]
    for err in iter_errors_iter:
        # Build a path like "json.id" or "items[3].name"
        path_parts: list[str] = []
        for p in err.absolute_path:
            if isinstance(p, int):
                path_parts.append(f"[{p}]")
            else:
                if path_parts:
                    path_parts.append(f".{p}")
                else:
                    path_parts.append(str(p))
        path = "".join(path_parts) or "<root>"
        errors.append(f"{path}: {err.message}")

    if errors:
        return False, errors
    return True, []


def schema_paths(
    schema: dict[str, Any] | None,
    *,
    prefix: str = "",
    max_depth: int = 6,
) -> set[str]:
    """Return the set of dotted attribute paths reachable through
    a JSON Schema's ``properties`` chain. Used by the copilot lint
    to verify ``node_X.foo.bar`` Jinja refs match what the handler
    actually produces.

    Examples::

        schema_paths({"type": "object", "properties": {"id": {"type": "string"},
                       "json": {"type": "object", "properties": {
                         "status": {"type": "string"}
                       }}}})
        # → {"id", "json", "json.status"}

    Limits:
      * ``max_depth`` caps recursion (default 6) so a recursive
        schema doesn't loop.
      * ``additionalProperties`` is NOT walked — we only know about
        explicitly declared paths. Refs that go through
        ``additionalProperties`` are accepted by the lint as
        "schema-permissive".
    """
    out: set[str] = set()
    if not isinstance(schema, dict) or max_depth <= 0:
        return out
    props = schema.get("properties")
    if not isinstance(props, dict):
        return out
    for key, sub in props.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        out.add(path)
        if isinstance(sub, dict):
            sub_type = sub.get("type")
            # Recurse into nested objects.
            if sub_type == "object" or "properties" in sub:
                out.update(schema_paths(sub, prefix=path, max_depth=max_depth - 1))
            # For arrays, walk into `items` if it's an object
            # schema, so {{ node_X.list[0].field }}-style refs
            # (which the regex captures as `node_X.list.field` —
            # square brackets aren't part of attr-path syntax)
            # are still reachable through the items schema.
            if sub_type == "array":
                items = sub.get("items")
                if isinstance(items, dict) and "properties" in items:
                    out.update(schema_paths(items, prefix=path, max_depth=max_depth - 1))
    return out


def schema_allows_path(
    schema: dict[str, Any] | None,
    path: str,
) -> bool:
    """Return True iff the dotted ``path`` is reachable through the
    schema's declared properties (or the schema is absent / non-
    restrictive).

    ``additionalProperties`` not False or an explicit
    ``"x-permissive": true`` extension means the lint stays quiet —
    authors might be deliberately schema-permissive.

    Empty path = "node itself" — always allowed.
    """
    if not schema:
        return True  # no schema declared = no constraint
    if not path:
        return True
    if not isinstance(schema, dict):
        return True
    # Permissive mode opt-out — authors with `additionalProperties:
    # true` (the JSON Schema default for object types, but explicit
    # is louder) are saying "I know there are more fields than
    # listed". Don't false-positive.
    if schema.get("additionalProperties") is True:
        return True
    if schema.get("x-permissive") is True:
        return True

    paths = schema_paths(schema)
    if path in paths:
        return True
    # Allow prefix match: a ref to `node_X.json` is fine even if
    # only `node_X.json.id` is declared (the parent path covers it).
    parent_paths = {p.rsplit(".", 1)[0] for p in paths if "." in p}
    if path in parent_paths:
        return True
    return False


def annotate_output_with_validation(
    output: Any,
    *,
    is_valid: bool,
    errors: list[str],
) -> Any:
    """When a dict output fails validation, stamp metadata onto it
    so downstream observability can flag the mismatch. Non-dict
    outputs are returned unchanged (we don't wrap them — that would
    surprise downstream code expecting the original type).

    Soft semantics — the original payload is preserved; only the
    metadata fields are added. Authors who want strict-fail mode
    set ``outputSchemaStrict: true`` on the node config; the engine
    raises rather than annotating in that case.
    """
    if is_valid:
        return output
    if not isinstance(output, dict):
        # Can't annotate a scalar / list without changing its type;
        # the caller's log captures the validation failure.
        return output
    annotated = dict(output)
    annotated["_schema_mismatch"] = True
    annotated["_schema_errors"] = errors[:5]  # cap to keep stub small
    return annotated


class OutputSchemaError(ValueError):
    """Raised when ``outputSchemaStrict: true`` is set and the
    handler's output fails validation."""
