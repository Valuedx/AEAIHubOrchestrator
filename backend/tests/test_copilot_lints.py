"""SMART-04 — unit tests for the copilot authoring lints.

Each rule gets its own happy-path + fires-when-expected test. The
structure lints (no_trigger / orphan_edge / disconnected_node) need
no DB; the ``missing_credential`` lint is tested with the
credentials resolver patched so we don't hit Postgres.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import lints


TENANT = "tenant-lints"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    *,
    label: str = "LLM Agent",
    category: str = "agent",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {
            "label": label,
            "nodeCategory": category,
            "config": dict(config or {}),
        },
    }


def _edge(src: str, tgt: str, *, edge_id: str | None = None) -> dict[str, Any]:
    return {
        "id": edge_id or f"edge_{src}_{tgt}",
        "source": src,
        "target": tgt,
    }


# ---------------------------------------------------------------------------
# no_trigger
# ---------------------------------------------------------------------------


def test_no_trigger_empty_graph_is_silent():
    # Empty graphs aren't broken — user is about to add nodes. Don't
    # harass them with a "missing trigger" nag.
    assert lints.lint_no_trigger({"nodes": [], "edges": []}) == []


def test_no_trigger_with_trigger_is_silent():
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node("node_2"),
        ],
        "edges": [_edge("node_1", "node_2")],
    }
    assert lints.lint_no_trigger(graph) == []


def test_no_trigger_without_trigger_fires_error():
    graph = {
        "nodes": [_node("node_1")],
        "edges": [],
    }
    out = lints.lint_no_trigger(graph)
    assert len(out) == 1
    assert out[0].code == "no_trigger"
    assert out[0].severity == "error"
    assert out[0].node_id is None
    assert "trigger" in out[0].fix_hint.lower()


# ---------------------------------------------------------------------------
# orphan_edge
# ---------------------------------------------------------------------------


def test_orphan_edge_fires_when_source_missing():
    graph = {
        "nodes": [_node("node_2")],
        "edges": [_edge("node_missing", "node_2", edge_id="edge_x")],
    }
    out = lints.lint_orphan_edges(graph)
    assert len(out) == 1
    assert out[0].code == "orphan_edge"
    assert out[0].severity == "error"
    assert "node_missing" in out[0].message


def test_orphan_edge_fires_when_target_missing():
    graph = {
        "nodes": [_node("node_1")],
        "edges": [_edge("node_1", "node_missing", edge_id="edge_y")],
    }
    out = lints.lint_orphan_edges(graph)
    assert len(out) == 1
    assert "node_missing" in out[0].message


def test_orphan_edge_silent_when_all_endpoints_exist():
    graph = {
        "nodes": [_node("node_1"), _node("node_2")],
        "edges": [_edge("node_1", "node_2")],
    }
    assert lints.lint_orphan_edges(graph) == []


# ---------------------------------------------------------------------------
# disconnected_node
# ---------------------------------------------------------------------------


def test_disconnected_node_fires_for_unreachable():
    # node_3 is in the graph but there's no path from the trigger.
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node("node_2"),
            _node("node_3", label="LLM Agent"),
        ],
        "edges": [_edge("node_1", "node_2")],
    }
    out = lints.lint_disconnected_nodes(graph)
    assert len(out) == 1
    assert out[0].code == "disconnected_node"
    assert out[0].severity == "warn"
    assert out[0].node_id == "node_3"


def test_disconnected_node_silent_when_all_reachable():
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node("node_2"),
            _node("node_3"),
        ],
        "edges": [
            _edge("node_1", "node_2"),
            _edge("node_2", "node_3"),
        ],
    }
    assert lints.lint_disconnected_nodes(graph) == []


def test_disconnected_node_silent_when_no_trigger():
    # If there's no trigger, lint_no_trigger handles it. We don't
    # double-surface disconnected_node complaints in that case
    # because EVERY node would be "disconnected" from nothing.
    graph = {
        "nodes": [_node("node_1"), _node("node_2")],
        "edges": [_edge("node_1", "node_2")],
    }
    assert lints.lint_disconnected_nodes(graph) == []


def test_disconnected_node_ignores_orphan_edges():
    """An edge that references a missing node shouldn't contribute to
    reachability — otherwise a dangling edge could hide a real
    disconnected-node bug."""
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node("node_isolated", label="LLM Agent"),
        ],
        "edges": [_edge("node_1", "node_missing")],
    }
    out = lints.lint_disconnected_nodes(graph)
    assert any(l.node_id == "node_isolated" for l in out)


def test_disconnected_triggers_themselves_are_not_flagged():
    """A trigger without incoming edges is fine — it's the entry
    point. Only non-trigger nodes that aren't reachable via the
    directed graph should surface."""
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node("node_2", label="Schedule Trigger", category="trigger"),
        ],
        "edges": [],
    }
    # Both are triggers — reachable from themselves; no warnings.
    assert lints.lint_disconnected_nodes(graph) == []


# ---------------------------------------------------------------------------
# missing_credential
# ---------------------------------------------------------------------------


def test_missing_credential_fires_when_source_is_not_configured():
    graph = {
        "nodes": [
            _node(
                "node_1", label="LLM Agent", category="agent",
                config={"provider": "google"},
            ),
        ],
        "edges": [],
    }
    with patch(
        "app.engine.llm_credentials_resolver.get_credentials_status",
        return_value={
            "google": {"source": "not_configured", "secret_name": "LLM_GOOGLE_API_KEY"},
            "openai": {"source": "env_default", "secret_name": "LLM_OPENAI_API_KEY"},
            "anthropic": {"source": "env_default", "secret_name": "LLM_ANTHROPIC_API_KEY"},
        },
    ):
        out = lints.lint_missing_credentials(
            graph, tenant_id=TENANT, db=MagicMock(),
        )
    assert len(out) == 1
    assert out[0].code == "missing_credential"
    assert out[0].severity == "error"
    assert out[0].node_id == "node_1"
    assert "google" in out[0].message.lower()


def test_missing_credential_silent_when_env_default_available():
    graph = {
        "nodes": [
            _node(
                "node_1", label="LLM Agent", category="agent",
                config={"provider": "anthropic"},
            ),
        ],
        "edges": [],
    }
    with patch(
        "app.engine.llm_credentials_resolver.get_credentials_status",
        return_value={
            "anthropic": {"source": "env_default", "secret_name": "LLM_ANTHROPIC_API_KEY"},
        },
    ):
        out = lints.lint_missing_credentials(
            graph, tenant_id=TENANT, db=MagicMock(),
        )
    assert out == []


def test_missing_credential_silent_for_vertex_provider():
    # Vertex uses ADC — no per-request key. The lint should skip.
    graph = {
        "nodes": [
            _node(
                "node_1", label="LLM Agent", category="agent",
                config={"provider": "vertex"},
            ),
        ],
        "edges": [],
    }
    out = lints.lint_missing_credentials(
        graph, tenant_id=TENANT, db=MagicMock(),
    )
    assert out == []


def test_missing_credential_skips_when_db_is_none():
    """Unit-test path: no DB available → return no lints rather than
    blow up. Production callers always have a db."""
    graph = {
        "nodes": [
            _node(
                "node_1", label="LLM Agent", category="agent",
                config={"provider": "google"},
            ),
        ],
        "edges": [],
    }
    out = lints.lint_missing_credentials(
        graph, tenant_id=TENANT, db=None,
    )
    assert out == []


def test_missing_credential_silent_when_no_llm_nodes():
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node("node_2", label="Notification", category="notification"),
        ],
        "edges": [_edge("node_1", "node_2")],
    }
    out = lints.lint_missing_credentials(
        graph, tenant_id=TENANT, db=MagicMock(),
    )
    # No LLM family nodes → no credential checks even if the provider
    # stash is empty.
    assert out == []


# ---------------------------------------------------------------------------
# run_lints composer
# ---------------------------------------------------------------------------


def test_run_lints_returns_combined_findings():
    # Trigger + disconnected node + LLM without credential — three
    # rules fire.
    graph = {
        "nodes": [
            _node("node_1", label="Webhook Trigger", category="trigger"),
            _node(
                "node_2", label="LLM Agent", category="agent",
                config={"provider": "google"},
            ),
            _node("node_isolated", label="LLM Agent", category="agent"),
        ],
        "edges": [_edge("node_1", "node_2")],
    }
    with patch(
        "app.engine.llm_credentials_resolver.get_credentials_status",
        return_value={
            "google": {"source": "not_configured", "secret_name": "LLM_GOOGLE_API_KEY"},
        },
    ):
        out = lints.run_lints(graph, tenant_id=TENANT, db=MagicMock())
    codes = {l.code for l in out}
    # no_trigger should NOT fire (webhook trigger present).
    assert "no_trigger" not in codes
    # disconnected_node fires for node_isolated.
    assert "disconnected_node" in codes
    # missing_credential fires for node_2.
    assert "missing_credential" in codes


def test_run_lints_empty_graph_is_clean():
    out = lints.run_lints(
        {"nodes": [], "edges": []},
        tenant_id=TENANT, db=None,
    )
    assert out == []
