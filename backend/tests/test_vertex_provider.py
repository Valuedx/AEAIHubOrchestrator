"""VERTEX-01 — Vertex AI provider for LLM / ReAct / streaming paths.

Vertex and AI Studio share all request/response code because both run
through the unified ``google-genai`` SDK — only the ``Client``
constructor differs. These tests lock in that contract at each of the
three call sites (``call_llm``, ``call_llm_streaming``,
``react_loop._PROVIDERS``) so a future refactor can't silently drop
Vertex while AI Studio keeps working.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fake response — mirrors the attributes llm_providers reads off
# of a real ``google-genai`` response without pulling in the whole SDK.
# ---------------------------------------------------------------------------


def _fake_response(text: str = "hi from vertex") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=3,
        ),
        candidates=[],
    )


def _fake_stream_chunks(tokens: list[str]):
    for token in tokens:
        yield SimpleNamespace(
            text=token,
            usage_metadata=None,
        )
    # Final chunk with usage totals — mirrors how the real SDK emits it.
    yield SimpleNamespace(
        text="",
        usage_metadata=SimpleNamespace(
            prompt_token_count=5,
            candidates_token_count=len(tokens),
        ),
    )


class _FakeGenaiClient:
    """Stand-in for ``google.genai.Client``. Records how it was
    constructed so tests can assert ``vertexai=True`` + project +
    location flowed through from settings.
    """

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.models = MagicMock()
        self.models.generate_content.return_value = _fake_response()
        self.models.generate_content_stream.return_value = iter(
            _fake_stream_chunks(["hello", " there"]),
        )


# ---------------------------------------------------------------------------
# _google_client — the factory both backends share
# ---------------------------------------------------------------------------


class TestGoogleClientFactory:
    def test_vertex_backend_initialises_client_with_vertexai_true(self):
        from app.config import settings
        from app.engine.llm_providers import _google_client

        with patch.object(settings, "vertex_project", "my-proj"), \
             patch.object(settings, "vertex_location", "europe-west4"), \
             patch("google.genai.Client", _FakeGenaiClient):
            client = _google_client("vertex")

        # Recorded constructor kwargs — this is the whole point of the
        # Vertex backend: ``vertexai=True`` + project + location, no api_key.
        assert client.init_kwargs == {
            "vertexai": True,
            "project": "my-proj",
            "location": "europe-west4",
        }

    def test_vertex_backend_raises_when_project_unset(self):
        from app.config import settings
        from app.engine.llm_providers import _google_client

        with patch.object(settings, "vertex_project", ""):
            with pytest.raises(ValueError, match="ORCHESTRATOR_VERTEX_PROJECT"):
                _google_client("vertex")

    def test_genai_backend_still_works(self):
        from app.config import settings
        from app.engine.llm_providers import _google_client

        with patch.object(settings, "google_api_key", "key-123"), \
             patch("google.genai.Client", _FakeGenaiClient):
            client = _google_client("genai")

        assert client.init_kwargs == {"api_key": "key-123"}

    def test_unknown_backend_raises(self):
        from app.engine.llm_providers import _google_client
        with pytest.raises(ValueError, match="Unknown google backend"):
            _google_client("bedrock")


# ---------------------------------------------------------------------------
# call_llm — dispatch + response shape
# ---------------------------------------------------------------------------


class TestCallLlmVertex:
    def test_dispatch_routes_vertex_through_genai_sdk(self):
        from app.config import settings
        from app.engine.llm_providers import call_llm

        captured: dict = {}

        class Recording(_FakeGenaiClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                captured.update(kwargs)

        with patch.object(settings, "vertex_project", "my-proj"), \
             patch.object(settings, "vertex_location", "us-central1"), \
             patch("google.genai.Client", Recording):
            result = call_llm(
                provider="vertex",
                model="gemini-2.5-flash",
                system_prompt="You are helpful.",
                user_message="hi",
            )

        # Provider field distinguishes Vertex from AI Studio in the log /
        # Langfuse trace even though the wire format is identical.
        assert result["provider"] == "vertex"
        assert result["model"] == "gemini-2.5-flash"
        assert result["response"] == "hi from vertex"
        assert result["usage"] == {"input_tokens": 10, "output_tokens": 3}
        # Client construction confirms the Vertex endpoint was used.
        assert captured.get("vertexai") is True
        assert captured.get("project") == "my-proj"

    def test_dispatch_rejects_vertex_without_project(self):
        from app.config import settings
        from app.engine.llm_providers import call_llm

        with patch.object(settings, "vertex_project", ""):
            with pytest.raises(ValueError, match="ORCHESTRATOR_VERTEX_PROJECT"):
                call_llm(
                    provider="vertex",
                    model="gemini-2.5-flash",
                    system_prompt="",
                    user_message="hi",
                )


# ---------------------------------------------------------------------------
# stream_vertex — same-backend streaming variant
# ---------------------------------------------------------------------------


class TestStreamVertex:
    def test_stream_vertex_publishes_tokens_and_matches_shape(self):
        from app.config import settings
        from app.engine.streaming_llm import stream_vertex

        with patch.object(settings, "vertex_project", "proj"), \
             patch.object(settings, "vertex_location", "us-central1"), \
             patch("google.genai.Client", _FakeGenaiClient), \
             patch("app.engine.streaming_llm.publish_token") as pub_tok, \
             patch("app.engine.streaming_llm.publish_stream_end") as pub_end:
            out = stream_vertex(
                model="gemini-2.5-flash",
                system_prompt="sys",
                user_message="user",
                temperature=0.5,
                max_tokens=100,
                instance_id="inst-1",
                node_id="node_5",
            )

        assert out["provider"] == "vertex"
        assert out["response"] == "hello there"
        # Tokens published per streamed chunk plus one end marker.
        assert pub_tok.call_count == 2
        pub_end.assert_called_once_with("inst-1", "node_5")

    def test_call_llm_streaming_routes_vertex(self):
        from app.config import settings
        from app.engine.llm_providers import call_llm_streaming

        with patch.object(settings, "vertex_project", "proj"), \
             patch("google.genai.Client", _FakeGenaiClient), \
             patch("app.engine.streaming_llm.publish_token"), \
             patch("app.engine.streaming_llm.publish_stream_end"):
            out = call_llm_streaming(
                provider="vertex",
                model="gemini-2.5-flash",
                system_prompt="",
                user_message="hi",
                instance_id="inst-2",
                node_id="node_7",
            )

        assert out["provider"] == "vertex"


# ---------------------------------------------------------------------------
# ReAct loop wiring
# ---------------------------------------------------------------------------


class TestReactLoopVertex:
    def test_providers_dict_has_vertex_entry(self):
        from app.engine.react_loop import _PROVIDERS

        assert "vertex" in _PROVIDERS
        entry = _PROVIDERS["vertex"]
        assert {"init", "call", "append_tool_results"}.issubset(entry.keys())

    def test_vertex_init_and_append_reuse_google_helpers(self):
        # Vertex and AI Studio differ only in the Client constructor, so
        # the ``init`` and ``append_tool_results`` hooks must be the
        # exact same callables. Guarantees future refactors can't
        # silently diverge the two.
        from app.engine.react_loop import _PROVIDERS

        assert _PROVIDERS["vertex"]["init"] is _PROVIDERS["google"]["init"]
        assert (
            _PROVIDERS["vertex"]["append_tool_results"]
            is _PROVIDERS["google"]["append_tool_results"]
        )

    def test_vertex_call_initialises_client_with_vertexai_true(self):
        from app.config import settings
        from app.engine.react_loop import _vertex_call, _google_init

        captured: dict = {}

        class Recording(_FakeGenaiClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                captured.update(kwargs)
                # react_loop._google_call_backend reads resp.candidates[0].content.parts
                self.models.generate_content.return_value = SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            content=SimpleNamespace(parts=[
                                SimpleNamespace(function_call=None, text="ok"),
                            ]),
                        ),
                    ],
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=4,
                        candidates_token_count=1,
                    ),
                )

        state = _google_init([{"role": "user", "content": "ping"}])

        with patch.object(settings, "vertex_project", "rp-proj"), \
             patch.object(settings, "vertex_location", "us-central1"), \
             patch("google.genai.Client", Recording):
            out = _vertex_call(
                model="gemini-2.5-pro",
                state=state,
                tools=[],
                temperature=0.7,
                max_tokens=256,
            )

        assert captured.get("vertexai") is True
        assert captured.get("project") == "rp-proj"
        assert out["content"] == "ok"
        assert out["tool_calls"] is None
