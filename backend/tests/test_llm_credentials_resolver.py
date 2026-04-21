"""ADMIN-03 — tests for engine/llm_credentials_resolver.

Covers:

* Precedence: tenant_secret → env fallback → missing (ValueError).
* Vault read failure degrades to env default with a log, does NOT
  raise — quota-style hot-path philosophy.
* ``get_credentials_status`` never returns the secret values; only
  source labels for the admin UI.
* ``tenant_id=None`` (internal paths) uses env defaults only.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def patched_settings():
    from app.config import settings

    with patch.object(settings, "google_api_key", "env-google-key"), \
         patch.object(settings, "openai_api_key", "env-openai-key"), \
         patch.object(settings, "openai_base_url", "https://api.openai.com/v1"), \
         patch.object(settings, "anthropic_api_key", "env-anthropic-key"):
        yield settings


@pytest.fixture
def vault():
    """Patch ``get_tenant_secret`` with a dict-backed fake."""
    store: dict[tuple[str, str], str] = {}

    def _get(tenant_id: str, key: str) -> str | None:
        return store.get((tenant_id, key))

    with patch("app.security.vault.get_tenant_secret", side_effect=_get):
        yield store


class TestPrecedence:
    def test_none_tenant_returns_env_default(self, patched_settings):
        from app.engine.llm_credentials_resolver import get_openai_api_key

        assert get_openai_api_key(None) == "env-openai-key"

    def test_tenant_secret_beats_env_default(self, patched_settings, vault):
        from app.engine.llm_credentials_resolver import get_openai_api_key

        vault[("tenant-a", "LLM_OPENAI_API_KEY")] = "sk-tenant-a-only"
        assert get_openai_api_key("tenant-a") == "sk-tenant-a-only"

    def test_separate_tenants_have_separate_keys(self, patched_settings, vault):
        from app.engine.llm_credentials_resolver import get_openai_api_key

        vault[("tenant-a", "LLM_OPENAI_API_KEY")] = "sk-a"
        vault[("tenant-b", "LLM_OPENAI_API_KEY")] = "sk-b"
        assert get_openai_api_key("tenant-a") == "sk-a"
        assert get_openai_api_key("tenant-b") == "sk-b"
        # A third tenant with no row falls through to env.
        assert get_openai_api_key("tenant-c") == "env-openai-key"

    def test_both_sides_missing_raises_with_remediation(self, monkeypatch, vault):
        from app.config import settings
        from app.engine.llm_credentials_resolver import get_openai_api_key

        monkeypatch.setattr(settings, "openai_api_key", "")

        with pytest.raises(ValueError) as exc:
            get_openai_api_key("tenant-a")

        msg = str(exc.value)
        # Message must name BOTH remediation paths so operators pick whichever fits.
        assert "LLM_OPENAI_API_KEY" in msg
        assert "ORCHESTRATOR_OPENAI_API_KEY" in msg

    def test_openai_base_url_always_returnable(self, patched_settings, vault):
        from app.engine.llm_credentials_resolver import get_openai_base_url

        # No tenant override → env default.
        assert get_openai_base_url("tenant-a") == "https://api.openai.com/v1"

        # Tenant override wins.
        vault[("tenant-a", "LLM_OPENAI_BASE_URL")] = "http://litellm.internal:4000/v1"
        assert get_openai_base_url("tenant-a") == "http://litellm.internal:4000/v1"


class TestGracefulDegrade:
    def test_vault_error_falls_back_to_env(self, patched_settings):
        """A broken vault (connection refused, RLS, Fernet rotation)
        must NOT hard-fail the LLM call — degrade to env default."""
        from app.engine.llm_credentials_resolver import get_openai_api_key

        def _explode(*_, **__):
            raise RuntimeError("vault down")

        with patch("app.security.vault.get_tenant_secret", side_effect=_explode):
            result = get_openai_api_key("tenant-a")

        assert result == "env-openai-key"


class TestStatusReport:
    def test_status_never_includes_values(self, patched_settings, vault):
        from app.engine.llm_credentials_resolver import get_credentials_status

        vault[("tenant-a", "LLM_OPENAI_API_KEY")] = "sk-supersecret"
        status = get_credentials_status("tenant-a")

        # Values must NEVER appear in the status dict — only source labels.
        flat = str(status)
        assert "sk-supersecret" not in flat
        assert status["openai"]["source"] == "tenant_secret"
        assert status["openai"]["secret_name"] == "LLM_OPENAI_API_KEY"

    def test_status_labels_cover_all_providers(self, patched_settings, vault):
        from app.engine.llm_credentials_resolver import get_credentials_status

        # Tenant has openai only; other providers fall through to env.
        vault[("tenant-a", "LLM_OPENAI_API_KEY")] = "sk-a"
        status = get_credentials_status("tenant-a")

        assert set(status.keys()) == {
            "google",
            "openai",
            "openai_base_url",
            "anthropic",
        }
        assert status["openai"]["source"] == "tenant_secret"
        assert status["google"]["source"] == "env_default"
        assert status["anthropic"]["source"] == "env_default"

    def test_status_reports_missing_when_both_sides_empty(self, monkeypatch, vault):
        from app.config import settings
        from app.engine.llm_credentials_resolver import get_credentials_status

        monkeypatch.setattr(settings, "google_api_key", "")
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key", "")

        status = get_credentials_status("tenant-a")
        assert status["google"]["source"] == "missing"
        assert status["openai"]["source"] == "missing"
        assert status["anthropic"]["source"] == "missing"


class TestIntegrationWithProviderCallSites:
    """Prove the resolver plugs into the _call_* paths without
    breaking the existing signatures."""

    def test_call_openai_uses_tenant_key(self, patched_settings, vault):
        # Skip if the openai SDK isn't installed locally — test is
        # about resolver → client wiring, not the SDK itself.
        pytest.importorskip("openai")

        from unittest.mock import MagicMock
        from app.engine.llm_providers import _call_openai

        vault[("tenant-a", "LLM_OPENAI_API_KEY")] = "sk-tenant-a"

        captured: dict = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.chat = MagicMock()
                self.chat.completions.create.return_value = MagicMock(
                    choices=[MagicMock(message=MagicMock(content="hi"))],
                    usage=MagicMock(prompt_tokens=1, completion_tokens=1),
                )

        with patch("openai.OpenAI", FakeClient):
            result = _call_openai(
                model="gpt-4o-mini",
                system_prompt="",
                user_message="hi",
                temperature=0.7,
                max_tokens=64,
                tenant_id="tenant-a",
            )

        assert captured["api_key"] == "sk-tenant-a"
        assert result["provider"] == "openai"

    def test_call_anthropic_uses_tenant_key(self, patched_settings, vault):
        pytest.importorskip("anthropic")

        from unittest.mock import MagicMock
        from app.engine.llm_providers import _call_anthropic

        vault[("tenant-a", "LLM_ANTHROPIC_API_KEY")] = "sk-ant-tenant-a"

        captured: dict = {}

        class FakeAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.messages = MagicMock()
                self.messages.create.return_value = MagicMock(
                    content=[MagicMock(type="text", text="hi")],
                    usage=MagicMock(input_tokens=1, output_tokens=1),
                )

        with patch("anthropic.Anthropic", FakeAnthropic):
            result = _call_anthropic(
                model="claude-3-5-haiku-20241022",
                system_prompt="",
                user_message="hi",
                temperature=0.7,
                max_tokens=64,
                tenant_id="tenant-a",
            )

        assert captured["api_key"] == "sk-ant-tenant-a"
        assert result["provider"] == "anthropic"
