"""Secret scrubber for execution log and observability payloads.

Recursively walks dict/list structures and redacts values whose key matches
a sensitive pattern (password, api_key, *_token, *_secret, authorization,
private_key, client_secret, bearer, access_key). Match is case-insensitive.

Design decisions:
  * Key-based only — we do not try to detect secret-looking strings by shape,
    because that produces both false positives (user data) and false negatives
    (API keys that don't match common shapes).
  * Wholesale redaction — if a key is sensitive, the whole value is replaced
    with REDACTED, even if it's a nested dict/list. Partial redaction of
    nested secret-bearing blobs is brittle.
  * Pure/functional — never mutates the input. Returns a new structure so the
    runtime context used by downstream nodes stays untouched.
"""

from __future__ import annotations

from typing import Any

REDACTED = "[REDACTED]"

# Exact key names (lowercased) that are always sensitive.
_SENSITIVE_EXACT: frozenset[str] = frozenset({
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "client_secret",
    "bearer",
    "auth",
    "cookie",
    "set_cookie",
    "credentials",
    "credential",
})

# Suffixes (lowercased) that mark a key as sensitive. Matched after replacing
# hyphens with underscores so X-API-Key, x_api_key and xApiKey all hit.
_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "_key",
    "_keys",
    "_secret",
    "_secrets",
    "_token",            # "access_token"; bare "tokens" stays benign for usage counters
    "_password",
    "_passwords",
    "_credentials",
    "_credential",
    "_auth",
    "_bearer",
    "_cookie",
    "_cookies",
)


def _normalize_key(key: str) -> str:
    return key.replace("-", "_").lower()


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    norm = _normalize_key(key)
    if norm in _SENSITIVE_EXACT:
        return True
    return any(norm.endswith(suf) for suf in _SENSITIVE_SUFFIXES)


def scrub_secrets(payload: Any) -> Any:
    """Return a copy of ``payload`` with values under sensitive keys replaced."""
    if isinstance(payload, dict):
        return {
            k: (REDACTED if is_sensitive_key(k) else scrub_secrets(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [scrub_secrets(x) for x in payload]
    if isinstance(payload, tuple):
        return tuple(scrub_secrets(x) for x in payload)
    return payload
