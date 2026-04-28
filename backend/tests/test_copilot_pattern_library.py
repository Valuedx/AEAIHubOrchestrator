"""SMART-02 — unit tests for the accepted-patterns library."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import pattern_library


TENANT = "tenant-patterns"


def _policy(enabled: bool = True):
    from app.engine.tenant_policy_resolver import EffectivePolicy
    return EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20,
        mcp_pool_size=4, rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        smart_02_pattern_library_enabled=enabled,
        smart_01_scenario_memory_enabled=False,
        smart_01_strict_promote_gate_enabled=False,
        smart_05_vector_docs_enabled=False,
        source={},
    )


def _graph(node_count: int = 2, labels: list[str] | None = None) -> dict:
    """Minimal graph with distinct labels + categories for lint-
    unrelated tests. Labels default to LLM Agent + Notification so
    node_type and tag extraction have something to chew on."""
    labels = labels or (["LLM Agent", "Notification"] * node_count)[:node_count]
    nodes = []
    for i, label in enumerate(labels):
        nodes.append({
            "id": f"node_{i + 1}",
            "type": "agenticNode",
            "data": {
                "label": label,
                "nodeCategory": "agent" if "Agent" in label else "notification",
                "config": {},
            },
        })
    edges = [
        {"id": f"edge_{i}", "source": f"node_{i}", "target": f"node_{i + 1}"}
        for i in range(1, len(nodes))
    ]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Token helpers (pure functions; no DB)
# ---------------------------------------------------------------------------


def test_tokenize_strips_stopwords_and_lowercases():
    assert pattern_library._tokenize("Build me a Slack SUMMARISER") == {
        "slack", "summariser",
    }


def test_tokenize_keeps_discriminative_words():
    """Stopword list is deliberately narrow — 'api' / 'data' /
    'user' are discriminative in workflow-authoring prose."""
    tokens = pattern_library._tokenize("user signup api flow")
    assert {"user", "signup", "api", "flow"}.issubset(tokens)


def test_tokenize_handles_none_and_empty():
    assert pattern_library._tokenize(None) == set()
    assert pattern_library._tokenize("") == set()


# ---------------------------------------------------------------------------
# Graph extraction
# ---------------------------------------------------------------------------


def test_extract_node_types_returns_distinct_sorted_labels():
    graph = _graph(labels=["LLM Agent", "LLM Agent", "Notification", "Webhook Trigger"])
    types = pattern_library._extract_node_types(graph)
    assert types == ["LLM Agent", "Notification", "Webhook Trigger"]


def test_extract_tags_combines_category_and_label_tokens():
    graph = _graph(labels=["LLM Agent", "Notification"])
    node_types = pattern_library._extract_node_types(graph)
    tags = pattern_library._extract_tags(graph, node_types)
    # Category tokens
    assert "agent" in tags
    assert "notification" in tags
    # Tokens from labels
    assert "llm" in tags
    assert "agent" in tags


def test_extract_node_types_ignores_nodes_without_label():
    graph = {
        "nodes": [
            {"id": "node_1", "type": "agenticNode", "data": {}},  # no label
            {"id": "node_2", "type": "agenticNode", "data": {"label": "LLM Agent"}},
        ],
        "edges": [],
    }
    assert pattern_library._extract_node_types(graph) == ["LLM Agent"]


# ---------------------------------------------------------------------------
# save_accepted_pattern — best-effort + opt-out
# ---------------------------------------------------------------------------


def test_save_accepted_pattern_persists_row_with_extracted_metadata():
    db = MagicMock()
    # No sessions → no intent; test the zero-intent path.
    db.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = None

    graph = _graph(node_count=3, labels=["Webhook Trigger", "LLM Agent", "Notification"])

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        pid = pattern_library.save_accepted_pattern(
            db,
            tenant_id=TENANT,
            source_draft_id=uuid.uuid4(),
            source_workflow_id=uuid.uuid4(),
            title="Slack summariser",
            graph_json=graph,
        )

    assert pid is not None
    # Row added.
    added = [c.args[0] for c in db.add.call_args_list]
    assert len(added) == 1
    row = added[0]
    # Denormalised metadata derived from the graph.
    assert row.title == "Slack summariser"
    assert row.node_count == 3
    assert row.edge_count == 2
    assert "LLM Agent" in row.node_types
    assert "Webhook Trigger" in row.node_types
    assert "agent" in row.tags
    assert row.nl_intent is None  # no session


def test_save_accepted_pattern_pulls_first_user_intent_from_session():
    from app.models.copilot import CopilotSession, CopilotTurn

    session = MagicMock(spec=CopilotSession)
    session.id = uuid.uuid4()
    first_turn = MagicMock(spec=CopilotTurn)
    first_turn.content_json = {"text": "build me a slack summariser"}

    db = MagicMock()
    # Sequence of first() calls: latest session, then first user turn.
    ordering = db.query.return_value.filter_by.return_value.order_by.return_value
    ordering.first.side_effect = iter([session, first_turn])

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        pattern_library.save_accepted_pattern(
            db,
            tenant_id=TENANT,
            source_draft_id=uuid.uuid4(),
            source_workflow_id=uuid.uuid4(),
            title="Slack summariser",
            graph_json=_graph(),
        )

    added = [c.args[0] for c in db.add.call_args_list]
    assert added[0].nl_intent == "build me a slack summariser"


def test_save_accepted_pattern_returns_none_when_flag_off():
    db = MagicMock()
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=False),
    ):
        pid = pattern_library.save_accepted_pattern(
            db,
            tenant_id=TENANT,
            source_draft_id=uuid.uuid4(),
            source_workflow_id=uuid.uuid4(),
            title="x",
            graph_json=_graph(),
        )
    assert pid is None
    # No rows touched when opted out.
    assert not db.add.called


def test_save_accepted_pattern_swallows_exceptions():
    """Pattern-save must never block a promote. A raising add() —
    simulating a unique-violation or connection drop — should be
    logged and return None, never propagate."""
    db = MagicMock()
    db.add.side_effect = RuntimeError("FK violation")
    db.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = None

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        pid = pattern_library.save_accepted_pattern(
            db,
            tenant_id=TENANT,
            source_draft_id=uuid.uuid4(),
            source_workflow_id=uuid.uuid4(),
            title="x",
            graph_json=_graph(),
        )
    assert pid is None


# ---------------------------------------------------------------------------
# recall_patterns — retrieval path
# ---------------------------------------------------------------------------


def _fake_row(*, title: str, nl_intent: str | None, tags: list[str], node_types: list[str]):
    from app.models.copilot import CopilotAcceptedPattern

    row = MagicMock(spec=CopilotAcceptedPattern)
    row.id = uuid.uuid4()
    row.title = title
    row.nl_intent = nl_intent
    row.tags = tags
    row.node_types = node_types
    row.node_count = len(node_types)
    row.edge_count = max(0, len(node_types) - 1)
    row.created_at = datetime.now(timezone.utc)
    row.graph_json = {"nodes": [], "edges": []}
    return row


def test_recall_patterns_ranks_best_match_first():
    """A "Slack summariser" pattern should outrank a "Webhook intake"
    pattern for a Slack-ish query. Both patterns overlap at least
    one query token so both appear in the results."""
    rows = [
        _fake_row(
            title="Webhook Slack intake",
            # Narrow overlap: only "slack" matches the query.
            nl_intent="Intake webhook messages from any service.",
            tags=["slack", "webhook"],
            node_types=["Webhook Trigger", "Notification"],
        ),
        _fake_row(
            title="Slack summariser",
            # Strong overlap: slack + messages + summarise.
            nl_intent="Summarise incoming Slack messages via email.",
            tags=["slack", "summariser", "email"],
            node_types=["Webhook Trigger", "LLM Agent", "Notification"],
        ),
    ]

    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = rows

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        out = pattern_library.recall_patterns(
            db, tenant_id=TENANT,
            query="summarise slack messages please",
            top_k=5,
        )

    assert out["enabled"] is True
    assert out["match_count"] == 2
    # Top result should be the Slack summariser (3 overlap + title).
    assert out["patterns"][0]["title"] == "Slack summariser"
    # Scores descend.
    scores = [p["score"] for p in out["patterns"]]
    assert scores == sorted(scores, reverse=True)
    # And the tighter match genuinely outranks the loose one.
    assert scores[0] > scores[1]


def test_recall_patterns_filters_zero_score_matches():
    """A query that doesn't overlap any token shouldn't return a
    tail of useless candidates."""
    rows = [
        _fake_row(
            title="Slack summariser",
            nl_intent="Summarise Slack messages.",
            tags=["slack"],
            node_types=["LLM Agent"],
        ),
    ]
    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = rows

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        out = pattern_library.recall_patterns(
            db, tenant_id=TENANT, query="xyzzy foobarbaz",
        )
    assert out["match_count"] == 0
    assert out["patterns"] == []


def test_recall_patterns_respects_flag_off():
    db = MagicMock()
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=False),
    ):
        out = pattern_library.recall_patterns(
            db, tenant_id=TENANT, query="slack",
        )
    assert out["enabled"] is False
    assert out["patterns"] == []
    # Never hits the DB when opted out.
    assert not db.query.called


def test_recall_patterns_clamps_top_k():
    rows = [
        _fake_row(
            title=f"Pattern {i}",
            nl_intent="slack",
            tags=["slack"],
            node_types=["LLM Agent"],
        )
        for i in range(20)
    ]
    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = rows

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        out = pattern_library.recall_patterns(
            db, tenant_id=TENANT, query="slack", top_k=9999,
        )
    assert len(out["patterns"]) <= pattern_library.SMART_02_RECALL_MAX_TOP_K


def test_recall_patterns_empty_query_is_silent():
    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = [
        _fake_row(title="x", nl_intent="y", tags=["z"], node_types=["LLM Agent"]),
    ]
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        out = pattern_library.recall_patterns(
            db, tenant_id=TENANT, query="",
        )
    assert out["enabled"] is True
    assert out["match_count"] == 0


def test_recall_patterns_exposes_graph_for_agent_adaptation():
    """The agent needs the full graph_json so it can directly adapt
    the pattern. Sanity-check it's returned, not stripped."""
    row = _fake_row(
        title="Slack summariser",
        nl_intent="summarise slack",
        tags=["slack"],
        node_types=["LLM Agent"],
    )
    row.graph_json = {
        "nodes": [{"id": "node_1", "type": "agenticNode", "data": {"label": "LLM Agent"}}],
        "edges": [],
    }
    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = [row]

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(enabled=True),
    ):
        out = pattern_library.recall_patterns(
            db, tenant_id=TENANT, query="slack",
        )
    assert out["patterns"][0]["graph_json"]["nodes"][0]["data"]["label"] == "LLM Agent"
