"""Unit tests for the secret scrubber."""

import pytest

from app.engine.scrubber import REDACTED, is_sensitive_key, scrub_secrets


class TestIsSensitiveKey:
    @pytest.mark.parametrize("key", [
        "password", "PASSWORD", "Passwd", "pwd",
        "secret", "Secret",
        "token", "TOKEN",
        "api_key", "apiKey", "API-Key", "X-API-Key", "apikey",
        "authorization", "Authorization",
        "access_key", "private_key", "client_secret",
        "bearer", "cookie", "set-cookie",
        "stripe_api_key", "db_password", "aws_access_key", "mcp_token",
        "llm_credentials", "oauth_credential", "foo_auth",
    ])
    def test_sensitive(self, key):
        assert is_sensitive_key(key), f"expected {key!r} to be flagged"

    @pytest.mark.parametrize("key", [
        "id", "name", "label", "description", "url", "endpoint",
        "prompt", "system_prompt", "model", "provider", "tokens_used",
        "config", "output", "input", "status", "message", "secretary",
    ])
    def test_benign(self, key):
        assert not is_sensitive_key(key), f"expected {key!r} to pass through"

    def test_non_string_key_is_benign(self):
        assert is_sensitive_key(42) is False
        assert is_sensitive_key(None) is False


class TestScrubSecrets:
    def test_flat_dict_redacts_sensitive_only(self):
        payload = {
            "api_key": "sk-live-abc",
            "model": "gpt-4",
            "temperature": 0.7,
        }
        out = scrub_secrets(payload)
        assert out == {"api_key": REDACTED, "model": "gpt-4", "temperature": 0.7}
        assert payload["api_key"] == "sk-live-abc", "original must not be mutated"

    def test_nested_dict(self):
        payload = {
            "config": {
                "provider": "openai",
                "openai_api_key": "sk-xxx",
                "headers": {"Authorization": "Bearer eyJ..."},
            },
            "trigger": {"user_message": "hi"},
        }
        out = scrub_secrets(payload)
        assert out["config"]["provider"] == "openai"
        assert out["config"]["openai_api_key"] == REDACTED
        assert out["config"]["headers"]["Authorization"] == REDACTED
        assert out["trigger"]["user_message"] == "hi"

    def test_list_of_dicts(self):
        payload = {
            "secrets": [
                {"name": "prod_db_password", "value": "p@ss"},
                {"name": "bucket", "value": "public"},
            ],
        }
        # 'secrets' is itself a sensitive key (exact match 'secret' matches via suffix _secrets? no — not suffix)
        # 'secrets' ends with 's' so not matched by _secret suffix. Keep as list.
        out = scrub_secrets(payload)
        assert out["secrets"][0]["name"] == "prod_db_password"
        assert out["secrets"][0]["value"] == "p@ss"  # 'value' is not sensitive
        # but prod_db_password is a key-value where *key* is sensitive — here
        # it's just a data value, not a dict key. Confirm:
        assert out["secrets"][1]["value"] == "public"

    def test_sensitive_key_redacts_nested_structure_wholesale(self):
        payload = {"credentials": {"user": "alice", "password": "p"}}
        out = scrub_secrets(payload)
        assert out["credentials"] == REDACTED

    def test_credentials_variants_are_sensitive(self):
        assert is_sensitive_key("credentials") is True
        assert is_sensitive_key("credential") is True
        assert is_sensitive_key("aws_credentials") is True
        # Benign 'tokens_used' remains passthrough so LLM usage counters
        # are still visible in logs.
        assert is_sensitive_key("tokens_used") is False

    def test_bearer_token_field(self):
        payload = {"headers": {"bearer_token": "eyJhbGc..."}}
        out = scrub_secrets(payload)
        assert out["headers"]["bearer_token"] == REDACTED

    def test_primitive_passthrough(self):
        assert scrub_secrets(None) is None
        assert scrub_secrets(42) == 42
        assert scrub_secrets("hello") == "hello"
        assert scrub_secrets([1, 2, 3]) == [1, 2, 3]

    def test_tuple_preserved(self):
        out = scrub_secrets({"opts": ("a", "b")})
        assert out["opts"] == ("a", "b")

    def test_sensitive_value_that_is_dict_redacted_fully(self):
        payload = {"api_key": {"oh": "no", "more": "secrets"}}
        out = scrub_secrets(payload)
        assert out["api_key"] == REDACTED

    def test_sensitive_value_that_is_list_redacted_fully(self):
        # Use an unambiguously-sensitive key. Bare "tokens" is intentionally
        # left benign so LLM token-usage lists don't get clobbered.
        payload = {"api_keys": ["k1", "k2", "k3"]}
        out = scrub_secrets(payload)
        assert out["api_keys"] == REDACTED
