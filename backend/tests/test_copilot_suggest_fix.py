"""COPILOT-03.c — unit tests for ``suggest_fix``.

The LLM subcall is mocked out so tests are hermetic. We pin:

  * Input validation (node_id / error required, node not in graph,
    unknown node_type surfaces as error).
  * Config-schema resolution through ``tool_layer.get_node_schema``
    (test uses a fake get_node_schema patched into runner_tools).
  * Patch filtering — keys outside config_schema.properties are
    moved to ``dropped_keys``.
  * Per-draft cap enforcement via prior-call count.
  * Confidence coercion (unknown values default to "medium").
  * JSON parse tolerance (code fences stripped, bad JSON → error).
  * ``applied`` is always ``False`` on success.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import runner_tools


TENANT = "tenant-suggest"


@dataclass
class _FakeDraft:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: str = TENANT
    graph_json: dict[str, Any] = field(default_factory=dict)
    version: int = 1


def _agentic_node(node_id: str, node_type: str, *, config: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {
            "label": node_type,
            "nodeType": node_type,
            "config": dict(config or {}),
        },
    }


def _mock_db_with_prior_count(count: int) -> MagicMock:
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.scalar.return_value = count
    return db


# ---------------------------------------------------------------------------
# _parse_suggest_fix_response
# ---------------------------------------------------------------------------


def test_parse_suggest_fix_strips_fenced_json():
    parsed = runner_tools._parse_suggest_fix_response(
        '```json\n{"patch": {"retries": 3}, "rationale": "r", "confidence": "high"}\n```'
    )
    assert parsed == {
        "patch": {"retries": 3},
        "rationale": "r",
        "confidence": "high",
    }


def test_parse_suggest_fix_accepts_plain_json():
    parsed = runner_tools._parse_suggest_fix_response(
        '{"patch": {}, "rationale": "unknown", "confidence": "low"}'
    )
    assert parsed["rationale"] == "unknown"


def test_parse_suggest_fix_returns_none_on_garbage():
    assert runner_tools._parse_suggest_fix_response("not json at all") is None
    assert runner_tools._parse_suggest_fix_response("") is None
    assert runner_tools._parse_suggest_fix_response("[1, 2, 3]") is None  # not a dict


# ---------------------------------------------------------------------------
# suggest_fix input validation
# ---------------------------------------------------------------------------


def test_suggest_fix_requires_node_id_and_error():
    draft = _FakeDraft()
    db = MagicMock()

    assert "error" in runner_tools.suggest_fix(
        db, tenant_id=TENANT, draft=draft, args={"error": "x"},
    )
    assert "error" in runner_tools.suggest_fix(
        db, tenant_id=TENANT, draft=draft, args={"node_id": "x"},
    )
    assert "error" in runner_tools.suggest_fix(
        db, tenant_id=TENANT, draft=draft,
        args={"node_id": "x", "error": 123},
    )


def test_suggest_fix_node_not_in_graph():
    draft = _FakeDraft(graph_json={"nodes": [], "edges": []})
    db = MagicMock()
    result = runner_tools.suggest_fix(
        db, tenant_id=TENANT, draft=draft,
        args={"node_id": "missing", "error": "boom"},
    )
    assert "not in draft graph" in result["error"]


def test_suggest_fix_unknown_node_type_surfaces_cleanly():
    draft = _FakeDraft(
        graph_json={
            "nodes": [_agentic_node("n1", "this_node_type_does_not_exist")],
            "edges": [],
        },
    )
    db = _mock_db_with_prior_count(0)
    result = runner_tools.suggest_fix(
        db, tenant_id=TENANT, draft=draft,
        args={"node_id": "n1", "error": "boom"},
    )
    assert "Unknown node_type" in result["error"]


# ---------------------------------------------------------------------------
# Happy path — mocks the LLM subcall + node-schema lookup
# ---------------------------------------------------------------------------


def _fake_schema(node_type: str) -> dict[str, Any]:
    return {
        "type": node_type,
        "category": "agent",
        "label": node_type,
        "description": "",
        "icon": None,
        "config_schema": {
            "properties": {
                "retries": {"type": "integer"},
                "model": {"type": "string"},
                "provider": {"type": "string"},
            },
        },
    }


def test_suggest_fix_happy_path_returns_proposal_and_never_applies():
    draft = _FakeDraft(
        graph_json={
            "nodes": [_agentic_node("n1", "llm_agent", config={"retries": 1})],
            "edges": [],
        },
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": (
                '{"patch": {"retries": 3, "provider": "anthropic"},'
                ' "rationale": "Bump retries and set provider.",'
                ' "confidence": "high"}'
            ),
            "usage": {"input_tokens": 200, "output_tokens": 50},
        },
    ):
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "no auth configured"},
        )

    assert result["applied"] is False
    assert result["proposed_patch"] == {"retries": 3, "provider": "anthropic"}
    assert result["rationale"].startswith("Bump retries")
    assert result["confidence"] == "high"
    assert result["dropped_keys"] == []


def test_suggest_fix_filters_keys_not_in_config_schema():
    draft = _FakeDraft(
        graph_json={
            "nodes": [_agentic_node("n1", "llm_agent")],
            "edges": [],
        },
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": (
                '{"patch": {"retries": 3, "hallucinated_field": "x",'
                ' "also_not_real": true},'
                ' "rationale": "r",'
                ' "confidence": "medium"}'
            ),
            "usage": {},
        },
    ):
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    assert result["proposed_patch"] == {"retries": 3}
    assert set(result["dropped_keys"]) == {"hallucinated_field", "also_not_real"}


def test_suggest_fix_coerces_unknown_confidence_to_medium():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": (
                '{"patch": {"model": "gpt-5"}, "rationale": "r",'
                ' "confidence": "very-high"}'
            ),
            "usage": {},
        },
    ):
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )
    assert result["confidence"] == "medium"


def test_suggest_fix_empty_patch_allowed_when_llm_has_no_idea():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": '{"patch": {}, "rationale": "no idea", "confidence": "low"}',
            "usage": {},
        },
    ):
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )
    assert result["proposed_patch"] == {}
    assert result["rationale"] == "no idea"


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


def test_suggest_fix_cap_blocks_further_calls():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_prior_count(runner_tools.MAX_SUGGEST_FIX_PER_DRAFT)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(runner_tools, "_call_suggest_fix_llm") as fake_llm:
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    assert "cap reached" in result["error"]
    assert result["prior_calls"] == runner_tools.MAX_SUGGEST_FIX_PER_DRAFT
    fake_llm.assert_not_called()  # cap gate short-circuited the LLM


# ---------------------------------------------------------------------------
# Parse failure
# ---------------------------------------------------------------------------


def test_suggest_fix_parse_failure_surfaces_as_error():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={"raw_text": "the model went freeform", "usage": {}},
    ):
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    assert "Could not parse" in result["error"]


def test_suggest_fix_llm_exception_surfaces_as_error():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm", side_effect=RuntimeError("anthropic down"),
    ):
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    assert "LLM call for suggest_fix failed" in result["error"]


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


def test_dispatch_routes_suggest_fix():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_prior_count(0)

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": '{"patch": {"retries": 2}, "rationale": "r", "confidence": "high"}',
            "usage": {},
        },
    ):
        result = runner_tools.dispatch(
            "suggest_fix",
            db=db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )
    assert result["proposed_patch"] == {"retries": 2}
    assert result["applied"] is False


# ---------------------------------------------------------------------------
# Provider routing — Anthropic / Google / Vertex (03.c Vertex parity)
# ---------------------------------------------------------------------------


def _mock_db_with_session(
    *,
    session_provider: str | None,
    session_model: str | None = None,
    prior_count: int = 0,
):
    """Stage the DB so _resolve_suggest_fix_provider sees a specific
    session, then the cap-count query sees ``prior_count``."""
    db = MagicMock()

    session = None
    if session_provider is not None:
        session = MagicMock()
        session.provider = session_provider
        session.model = session_model

    # First query branch: CopilotSession ordered by created_at desc
    # → .first()
    # Second query branch: cap count via func.count(...) → .scalar()
    session_chain = MagicMock()
    session_chain.order_by.return_value.first.return_value = session

    count_chain = MagicMock()
    count_chain.scalar.return_value = prior_count

    def _query_side_effect(arg):
        name = getattr(arg, "__name__", "")
        # The join+filter chain on CopilotTurn → scalar() path is the
        # per-draft cap check. CopilotSession.filter_by → first() is
        # the provider-resolver path.
        if name == "CopilotSession":
            result = MagicMock()
            result.filter_by.return_value = session_chain
            return result
        # count(...) path — the call site uses db.query(func.count(...))
        result = MagicMock()
        result.join.return_value.filter.return_value = count_chain
        return result

    db.query.side_effect = _query_side_effect
    return db


def test_suggest_fix_uses_session_provider_for_vertex():
    """Vertex session on the draft → suggest_fix must call the
    Google/Vertex path, not Anthropic."""
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_session(
        session_provider="vertex",
        session_model="gemini-3.1-pro-preview-customtools",
        prior_count=0,
    )

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": '{"patch": {"retries": 5}, "rationale": "r", "confidence": "high"}',
            "usage": {},
        },
    ) as fake_llm:
        result = runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    _, kwargs = fake_llm.call_args
    assert kwargs["provider"] == "vertex"
    assert kwargs["model"] == "gemini-3.1-pro-preview-customtools"
    # Result surfaces provider + model so agent narration + UI can
    # show which engine proposed the fix.
    assert result["provider"] == "vertex"
    assert result["model"] == "gemini-3.1-pro-preview-customtools"


def test_suggest_fix_uses_session_provider_for_google():
    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_session(
        session_provider="google",
        session_model="gemini-3.1-pro-preview-customtools",
    )

    with patch.object(
        runner_tools, "get_node_schema", return_value=_fake_schema("llm_agent"),
    ), patch.object(
        runner_tools, "_call_suggest_fix_llm",
        return_value={
            "raw_text": '{"patch": {}, "rationale": "", "confidence": "low"}',
            "usage": {},
        },
    ) as fake_llm:
        runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    _, kwargs = fake_llm.call_args
    assert kwargs["provider"] == "google"


def test_suggest_fix_falls_back_to_settings_default_when_no_session():
    """No active session on the draft → use settings.copilot_default_provider.
    Flipping the env default to vertex makes suggest_fix Vertex-by-default."""
    from app.config import settings

    draft = _FakeDraft(
        graph_json={"nodes": [_agentic_node("n1", "llm_agent")], "edges": []},
    )
    db = _mock_db_with_session(session_provider=None, prior_count=0)

    with patch.object(settings, "copilot_default_provider", "vertex"), \
         patch.object(
            runner_tools, "get_node_schema",
            return_value=_fake_schema("llm_agent"),
         ), patch.object(
            runner_tools, "_call_suggest_fix_llm",
            return_value={
                "raw_text": '{"patch": {"retries": 1}, "rationale": "r", "confidence": "medium"}',
                "usage": {},
            },
         ) as fake_llm:
        runner_tools.suggest_fix(
            db, tenant_id=TENANT, draft=draft,
            args={"node_id": "n1", "error": "boom"},
        )

    _, kwargs = fake_llm.call_args
    assert kwargs["provider"] == "vertex"
    # Default model pulled from DEFAULT_MODEL_BY_PROVIDER — Gemini 3.x.
    assert kwargs["model"].startswith("gemini-3")


def test_suggest_fix_google_call_dispatches_through_shared_genai_client():
    """Functional: _call_suggest_fix_llm with provider='vertex' must
    hit the shared _google_client path (VERTEX-01 parity) instead of
    the Anthropic SDK."""
    from google.genai import types  # noqa: F401 — import guard

    with patch(
        "app.engine.llm_providers._google_client",
    ) as fake_client_factory:
        client = MagicMock()
        part = MagicMock()
        part.text = '{"patch": {"retries": 3}, "rationale": "r", "confidence": "high"}'
        candidate = MagicMock()
        candidate.content.parts = [part]
        resp = MagicMock()
        resp.candidates = [candidate]
        resp.usage_metadata.prompt_token_count = 120
        resp.usage_metadata.candidates_token_count = 40
        client.models.generate_content.return_value = resp
        fake_client_factory.return_value = client

        result = runner_tools._call_suggest_fix_llm(
            tenant_id=TENANT,
            node_type="llm_agent",
            node_id="n1",
            error="boom",
            current_config={},
            config_schema={"properties": {"retries": {"type": "integer"}}},
            provider="vertex",
            model="gemini-3.1-pro-preview-customtools",
        )

    # Vertex backend was requested + tenant id threaded through for
    # VERTEX-02 per-tenant project resolution.
    fake_client_factory.assert_called_once_with("vertex", tenant_id=TENANT)
    # Gemini 3.x model pinned on the Vertex call.
    _, call_kwargs = client.models.generate_content.call_args
    assert call_kwargs["model"] == "gemini-3.1-pro-preview-customtools"
    assert "retries" in result["raw_text"]
    assert result["usage"]["input_tokens"] == 120


def test_suggest_fix_rejects_unsupported_provider():
    with pytest.raises(ValueError, match="unsupported provider"):
        runner_tools._call_suggest_fix_llm(
            tenant_id=TENANT,
            node_type="llm_agent", node_id="n1", error="x",
            current_config={}, config_schema={},
            provider="openai",  # not wired in this slice
        )
