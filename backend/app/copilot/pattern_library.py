"""SMART-02 — per-tenant accepted-patterns library.

Every successful ``/promote`` persists a snapshot of the accepted
graph + the originating NL intent into ``copilot_accepted_patterns``.
The agent then retrieves the nearest 2–3 patterns for the same
tenant on future drafts and uses them as few-shot — which is how the
copilot starts to sound like the tenant's own conventions (naming,
preferred MCP servers, memory profiles) rather than a generic
builder.

Design contract
---------------

Save path (``save_accepted_pattern``)

  * Best-effort. Wrapped in a try/except by the promote endpoint so
    a pattern-save failure never blocks a promote. The draft is
    already deleted by the time the save runs, so losing one
    pattern row is survivable.
  * NL intent is sourced from the first ``user`` turn of the
    draft's most recent session. If the draft was edited purely by
    hand (no session), we still save the pattern but leave
    ``nl_intent`` null — retrieval scoring will just weight it as
    an untagged candidate.
  * Tags + node types are extracted from the graph at save time so
    retrieval doesn't need to re-walk every graph on each query.

Retrieval path (``recall_patterns``)

  * Index-backed top-N candidate fetch (most recent 50 rows per
    tenant) + in-memory token-overlap score. This is the same
    shape as ``docs_index`` in SMART-01b.iii.
  * Query tokens = tokens of the caller-supplied `nl_intent` (the
    agent passes the user's most recent message). Stopwords are
    stripped.
  * Candidate tokens = union of the pattern's tokenised nl_intent
    + tags + node_types. Title matches get a 2× boost so a
    "Slack summariser" pattern retrieves cleanly for a "summarise
    Slack messages" query even when the body doesn't repeat the
    word.
  * Returns the top-``top_k`` candidates (default 3, max 10) with
    their full graph_json so the agent can directly adapt them.

Opt-out
-------

Both paths check ``smart_02_pattern_library_enabled`` on the tenant
policy. When off, ``save_accepted_pattern`` is a no-op (returns
``None``) and ``recall_patterns`` returns an empty result with
``enabled: false`` so the agent can narrate "no prior patterns
available" if it wants.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning knobs (exposed as constants so tests can patch without touching
# production defaults).
# ---------------------------------------------------------------------------


SMART_02_RETRIEVAL_CANDIDATES = 50
"""Top-N candidate rows to pull from the DB before in-memory
ranking. Tenants with fewer than this many patterns will simply
score all of their rows — the limit is there so a tenant with
thousands of promoted patterns doesn't pay an O(n) scan per query.
"""

SMART_02_RECALL_DEFAULT_TOP_K = 3
SMART_02_RECALL_MAX_TOP_K = 10


# Stopword list tuned for workflow-authoring intent prose. Keeps
# terms like "api", "data", "user" because they're genuinely
# discriminative ("user signup flow" vs. "error monitoring flow").
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for",
    "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "have", "has", "had", "with", "without", "by", "as", "at",
    "if", "this", "that", "these", "those", "it", "its", "from",
    "how", "what", "when", "where", "why", "who", "which",
    "can", "should", "would", "could", "may", "might", "will",
    "you", "your", "we", "our", "they", "their", "my", "me",
    "build", "builds", "building", "built",
    "make", "makes", "making", "made",
    "create", "creates", "creating", "created",
    "want", "wants", "wanted", "need", "needs", "needed",
})


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS
    }


# ---------------------------------------------------------------------------
# Extraction helpers (used by save_accepted_pattern)
# ---------------------------------------------------------------------------


def _extract_node_types(graph_json: dict[str, Any]) -> list[str]:
    """Distinct, sorted list of the registry ``label`` values used
    by the graph. This is what the node_registry validator keys on
    and what the agent's system prompt will see when retrieving."""
    labels: set[str] = set()
    for node in graph_json.get("nodes") or []:
        data = node.get("data") or {}
        label = data.get("label")
        if isinstance(label, str) and label:
            labels.add(label)
    return sorted(labels)


def _extract_tags(graph_json: dict[str, Any], node_types: list[str]) -> list[str]:
    """Category + label tokens. Tokens (not full strings) so
    overlap scoring against a free-form query is straightforward."""
    tokens: set[str] = set()
    for node in graph_json.get("nodes") or []:
        data = node.get("data") or {}
        category = data.get("nodeCategory")
        if isinstance(category, str) and category:
            tokens.add(category.lower())
    for label in node_types:
        tokens |= _tokenize(label)
    return sorted(tokens)


def _first_user_intent(
    db: Session, *, tenant_id: str, draft_id: uuid.UUID,
) -> str | None:
    """Return the text of the first ``user`` turn of the draft's
    most recent session, or ``None`` if no session exists (manual
    edit path)."""
    from app.models.copilot import CopilotSession, CopilotTurn

    latest = (
        db.query(CopilotSession)
        .filter_by(tenant_id=tenant_id, draft_id=draft_id)
        .order_by(CopilotSession.created_at.desc())
        .first()
    )
    if latest is None:
        return None

    first_user = (
        db.query(CopilotTurn)
        .filter_by(
            tenant_id=tenant_id,
            session_id=latest.id,
            role="user",
        )
        .order_by(CopilotTurn.turn_index)
        .first()
    )
    if first_user is None:
        return None
    content = first_user.content_json or {}
    text = content.get("text") if isinstance(content, dict) else None
    return text if isinstance(text, str) else None


# ---------------------------------------------------------------------------
# Save path — called from promote after the new WorkflowDefinition commits
# ---------------------------------------------------------------------------


def save_accepted_pattern(
    db: Session,
    *,
    tenant_id: str,
    source_draft_id: uuid.UUID,
    source_workflow_id: uuid.UUID,
    title: str,
    graph_json: dict[str, Any],
    created_by: str | None = None,
) -> str | None:
    """Persist a pattern row for the just-promoted draft.

    Returns the new pattern id on success, ``None`` if the tenant
    has opted out (flag off) or the save failed. Never raises —
    the caller expects this to be best-effort.
    """
    from app.engine.tenant_policy_resolver import get_effective_policy
    from app.models.copilot import CopilotAcceptedPattern

    try:
        policy = get_effective_policy(tenant_id)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "SMART-02 save_accepted_pattern: policy resolver failed "
            "for tenant=%r: %s", tenant_id, exc,
        )
        return None

    if not policy.smart_02_pattern_library_enabled:
        return None

    try:
        node_types = _extract_node_types(graph_json)
        tags = _extract_tags(graph_json, node_types)
        intent = _first_user_intent(
            db, tenant_id=tenant_id, draft_id=source_draft_id,
        )
        nodes_len = len(graph_json.get("nodes") or [])
        edges_len = len(graph_json.get("edges") or [])

        pattern = CopilotAcceptedPattern(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_draft_id=source_draft_id,
            source_workflow_id=source_workflow_id,
            title=title,
            nl_intent=intent,
            graph_json=graph_json,
            node_types=node_types,
            tags=tags,
            node_count=nodes_len,
            edge_count=edges_len,
            created_by=created_by,
        )
        db.add(pattern)
        db.flush()  # caller owns commit — the enclosing promote tx
        return str(pattern.id)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "SMART-02 save_accepted_pattern: save failed for tenant=%r "
            "draft=%r: %s",
            tenant_id, source_draft_id, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Retrieval path — used by the recall_patterns runner tool
# ---------------------------------------------------------------------------


@dataclass
class PatternScore:
    """Internal helper — the scored candidate tuple ranking returns."""

    pattern_id: str
    title: str
    score: float
    nl_intent: str | None
    tags: list[str]
    node_types: list[str]
    node_count: int
    edge_count: int
    created_at: str | None
    graph_json: dict[str, Any]


def _candidate_tokens(pattern_row: Any) -> set[str]:
    """Build the pattern's scoring token set from nl_intent + tags +
    node_types + title."""
    tokens: set[str] = set()
    tokens |= _tokenize(pattern_row.nl_intent)
    tokens |= _tokenize(pattern_row.title)
    for tag in pattern_row.tags or []:
        if isinstance(tag, str):
            tokens.add(tag.lower())
    for node_type in pattern_row.node_types or []:
        tokens |= _tokenize(node_type)
    return tokens


def _score_pattern(query: set[str], pattern_row: Any) -> float:
    """Match score: overlap of (query, pattern-tokens) with a 2×
    title boost. Zero means no meaningful match — those candidates
    are dropped rather than returned at the tail of the list."""
    if not query:
        return 0.0
    body_tokens = _candidate_tokens(pattern_row)
    body_overlap = len(query & body_tokens)
    title_overlap = len(query & _tokenize(pattern_row.title))
    if body_overlap == 0 and title_overlap == 0:
        return 0.0
    return float(body_overlap + title_overlap)


def recall_patterns(
    db: Session,
    *,
    tenant_id: str,
    query: str,
    top_k: int = SMART_02_RECALL_DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Return the most relevant accepted patterns for the tenant
    given a free-form query (the user's NL intent).

    Shape::

        {
          "enabled": bool,
          "query": str,
          "match_count": int,
          "patterns": [
            {
              "id", "title", "score", "nl_intent",
              "tags", "node_types", "node_count", "edge_count",
              "created_at", "graph_json"
            },
            ...
          ]
        }

    When the tenant has opted out, ``enabled`` is false and
    ``patterns`` is empty.
    """
    from app.engine.tenant_policy_resolver import get_effective_policy
    from app.models.copilot import CopilotAcceptedPattern

    try:
        policy = get_effective_policy(tenant_id)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "SMART-02 recall_patterns: policy resolver failed for "
            "tenant=%r: %s", tenant_id, exc,
        )
        return {"enabled": False, "query": query, "match_count": 0, "patterns": []}

    if not policy.smart_02_pattern_library_enabled:
        return {"enabled": False, "query": query, "match_count": 0, "patterns": []}

    k = max(1, min(int(top_k or SMART_02_RECALL_DEFAULT_TOP_K), SMART_02_RECALL_MAX_TOP_K))
    q_tokens = _tokenize(query)

    rows = (
        db.query(CopilotAcceptedPattern)
        .filter_by(tenant_id=tenant_id)
        .order_by(CopilotAcceptedPattern.created_at.desc())
        .limit(SMART_02_RETRIEVAL_CANDIDATES)
        .all()
    )

    scored: list[tuple[float, Any]] = []
    for row in rows:
        score = _score_pattern(q_tokens, row)
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:k]

    return {
        "enabled": True,
        "query": query,
        "match_count": len(scored),
        "patterns": [
            {
                "id": str(row.id),
                "title": row.title,
                "score": score,
                "nl_intent": row.nl_intent,
                "tags": list(row.tags or []),
                "node_types": list(row.node_types or []),
                "node_count": row.node_count,
                "edge_count": row.edge_count,
                "created_at": (
                    row.created_at.isoformat() if row.created_at else None
                ),
                "graph_json": row.graph_json,
            }
            for score, row in top
        ],
    }
