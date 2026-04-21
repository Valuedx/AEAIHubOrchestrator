"""ADMIN-03 — tests for the /api/v1/llm-credentials status endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.llm_credentials import router as llm_credentials_router


TENANT = "tenant-a"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(llm_credentials_router, prefix="/api/v1/llm-credentials")

    from app.security.tenant import get_tenant_id
    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    return TestClient(app)


@pytest.fixture
def patched_settings():
    from app.config import settings

    with patch.object(settings, "google_api_key", "env-google"), \
         patch.object(settings, "openai_api_key", "env-openai"), \
         patch.object(settings, "openai_base_url", "https://api.openai.com/v1"), \
         patch.object(settings, "anthropic_api_key", "env-anthropic"):
        yield settings


class TestStatusEndpoint:
    def test_reports_env_defaults_when_no_tenant_secrets(self, client, patched_settings):
        with patch("app.security.vault.get_tenant_secret", return_value=None):
            res = client.get("/api/v1/llm-credentials")

        assert res.status_code == 200
        body = res.json()
        assert body["tenant_id"] == TENANT
        assert body["providers"]["google"]["source"] == "env_default"
        assert body["providers"]["openai"]["source"] == "env_default"
        assert body["providers"]["anthropic"]["source"] == "env_default"

    def test_reports_tenant_secret_when_set(self, client, patched_settings):
        def _vault(tenant_id, key):
            if key == "LLM_OPENAI_API_KEY":
                return "sk-tenant"
            return None

        with patch("app.security.vault.get_tenant_secret", side_effect=_vault):
            res = client.get("/api/v1/llm-credentials")

        body = res.json()
        assert body["providers"]["openai"]["source"] == "tenant_secret"
        # Other providers still env.
        assert body["providers"]["google"]["source"] == "env_default"

    def test_reports_missing_when_both_sides_empty(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "google_api_key", "")
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key", "")

        with patch("app.security.vault.get_tenant_secret", return_value=None):
            res = client.get("/api/v1/llm-credentials")

        body = res.json()
        assert body["providers"]["google"]["source"] == "missing"
        assert body["providers"]["openai"]["source"] == "missing"
        assert body["providers"]["anthropic"]["source"] == "missing"

    def test_never_leaks_secret_values_in_response(self, client, patched_settings):
        """A bug that accidentally started including the key value in
        the response would be a credential-exposure incident. Lock
        this down with an explicit assertion."""
        def _vault(tenant_id, key):
            if key == "LLM_OPENAI_API_KEY":
                return "sk-super-secret-do-not-leak"
            return None

        with patch("app.security.vault.get_tenant_secret", side_effect=_vault):
            res = client.get("/api/v1/llm-credentials")

        # The entire response body must NOT contain the secret value.
        assert "sk-super-secret-do-not-leak" not in res.text
        # Neither should the env defaults leak.
        assert "env-openai" not in res.text
        assert "env-google" not in res.text
        assert "env-anthropic" not in res.text
