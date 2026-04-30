"""CTX-MGMT.B — Jinja / safe_eval node-reference extraction.

Pure helper. Walks an arbitrary string and returns every reference of
the shape ``node_X`` or ``node_X.foo.bar`` it can find. Used by the
``lint_jinja_dangling_reference`` SMART-04 lint to catch typos and
broken cross-references at promote time, before they ship as silent
empty-string renderings at runtime.

Why one regex covers both languages
-----------------------------------

The orchestrator uses two different expression languages:

  * **Jinja2** — ``{{ node_4r.json.id }}`` in `systemPrompt`,
    `body` (HTTP), `url`, header values.
  * **safe_eval** — ``node_4r.json.id`` in Switch/Condition
    `expression`, ForEach `arrayExpression`, Loop
    `continueExpression`, Bridge `messageExpression`.

Both share the same dotted-attribute syntax for node references —
``node_<id>(.<attr>)*``. A single regex extracts both shapes from
any string, which is enough for the existence-check our v1 lint
needs. Reachability analysis (Switch arm pruning) is more involved
and deferred to v2 — see CTX-MGMT.B v2 in
``codewiki/context-management-plan.md``.

False positives
---------------

The regex would also match a literal ``node_X`` mention in a
docstring or comment. In practice this isn't a concern because the
lint scans node ``config`` JSON values (not source code or
documentation), and ``node_<id>`` names in user-authored config
text are vanishingly rare outside actual references. If it becomes
a problem, the lint can be tightened to require the surrounding
``{{...}}`` / templating context.

Public surface
--------------

  * ``extract_node_refs(text)`` — returns ``list[(node_id,
    attr_path_str)]``. ``attr_path_str`` is the dotted suffix
    (``"json.id"`` for ``node_4r.json.id``) or empty string for
    bare ``node_X`` references.
  * ``walk_node_strings(obj)`` — helper that yields every string
    value in a JSON-shaped object (dict / list / scalar). Used to
    feed the lint over each node's full config.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Match `node_<id>` optionally followed by `.attr.attr...`.
# Use a positive lookbehind to require either start-of-string or a
# non-word char before — so `not_a_node_X_thing` doesn't match.
_NODE_REF_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"          # left boundary (no-word-before)
    r"(node_[A-Za-z0-9_]+)"       # the node id
    r"((?:\.[A-Za-z_][A-Za-z0-9_]*)*)"  # optional .attr.attr.attr...
)


def extract_node_refs(text: str) -> list[tuple[str, str]]:
    """Return every node reference in ``text``.

    Output: ``[(node_id, attr_path), ...]`` where ``attr_path`` is
    the dotted suffix without the leading dot. Empty string for
    bare ``node_X`` references (no attribute access).

    Examples::

        extract_node_refs("{{ node_4r.json.id }}")
        # → [("node_4r", "json.id")]

        extract_node_refs("Hello {{ trigger.x }} from {{ node_2.messages }}")
        # → [("node_2", "messages")]

        extract_node_refs("if node_3.branch == 'true' else node_4r.json")
        # → [("node_3", "branch"), ("node_4r", "json")]

        extract_node_refs("{{ node_X }}")
        # → [("node_X", "")]
    """
    if not isinstance(text, str) or "node_" not in text:
        return []
    out: list[tuple[str, str]] = []
    for match in _NODE_REF_PATTERN.finditer(text):
        node_id = match.group(1)
        attr_suffix = match.group(2) or ""
        # Strip the leading dot from the attr_path.
        if attr_suffix.startswith("."):
            attr_suffix = attr_suffix[1:]
        out.append((node_id, attr_suffix))
    return out


def walk_node_strings(obj: Any) -> Iterable[str]:
    """Yield every string value found inside a JSON-shaped object.

    Walks dicts (yields values), lists (yields each element's strings),
    and ignores scalars that aren't strings. Used by the lint to
    sweep every templated field of a node config in one pass — we
    don't have to maintain a list of "fields that may contain Jinja"
    that drifts as new node types ship.
    """
    if isinstance(obj, str):
        if obj:
            yield obj
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_node_strings(v)
        return
    if isinstance(obj, (list, tuple)):
        for v in obj:
            yield from walk_node_strings(v)
        return
    # Scalars (int, float, bool, None) — skip.
    return


def collect_refs_from_config(config: Any) -> list[tuple[str, str]]:
    """Walk a node's full config and return every node reference
    found across all string values. De-duplicated."""
    seen: set[tuple[str, str]] = set()
    for s in walk_node_strings(config):
        for ref in extract_node_refs(s):
            seen.add(ref)
    return sorted(seen)
