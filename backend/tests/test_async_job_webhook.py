"""Unit tests for the async-jobs webhook endpoint and its signature
helpers.

The endpoint itself is tested via FastAPI's TestClient with a
dependency-injected in-memory async_jobs row + a mocked resume
dispatch. Signature helpers are pure and tested in isolation.
"""

from __future__ import annotations

import hmac
import json
import uuid
from hashlib import sha256
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.async_jobs import (
    compute_hmac_sha256,
    router as async_jobs_router,
    verify_hmac,
    verify_token,
)


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

class TestVerifyToken:
    def test_matching_tokens_accepted(self):
        assert verify_token("abc123", "abc123") is True

    def test_mismatched_tokens_rejected(self):
        assert verify_token("abc123", "abc124") is False

    def test_none_or_empty_rejected(self):
        for provided, expected in [
            (None, "abc"),
            ("abc", None),
            (None, None),
            ("", "abc"),
            ("abc", ""),
            ("", ""),
        ]:
            assert verify_token(provided, expected) is False, (provided, expected)

    def test_long_token_still_constant_time(self):
        # Not a direct timing test — but verify_token must return a bool
        # regardless of length (guards against accidental substring tricks).
        a = "x" * 512
        b = "x" * 512
        assert verify_token(a, b) is True
        assert verify_token(a + "y", b) is False


class TestVerifyHmac:
    SECRET = "shared-secret"
    BODY = b'{"status":"Complete"}'

    def _sig(self, prefix="sha256="):
        mac = hmac.new(self.SECRET.encode(), self.BODY, sha256).hexdigest()
        return prefix + mac

    def test_accepts_sha256_prefixed_signature(self):
        assert verify_hmac(self._sig(), self.BODY, self.SECRET) is True

    def test_accepts_bare_hex_without_prefix(self):
        assert verify_hmac(self._sig(prefix=""), self.BODY, self.SECRET) is True

    def test_rejects_wrong_signature(self):
        assert verify_hmac("sha256=deadbeef", self.BODY, self.SECRET) is False

    def test_rejects_signature_over_different_body(self):
        sig = self._sig()
        assert verify_hmac(sig, b'{"status":"Failure"}', self.SECRET) is False

    def test_rejects_signature_with_wrong_secret(self):
        assert verify_hmac(self._sig(), self.BODY, "other-secret") is False

    def test_rejects_missing_pieces(self):
        sig = self._sig()
        for args in [
            (None, self.BODY, self.SECRET),
            (sig, b"", self.SECRET),
            (sig, self.BODY, None),
            (sig, self.BODY, ""),
            ("", self.BODY, self.SECRET),
        ]:
            assert verify_hmac(*args) is False, args


class TestComputeHmac:
    def test_matches_stdlib(self):
        secret, body = "k", b"hello"
        expected = hmac.new(secret.encode(), body, sha256).hexdigest()
        assert compute_hmac_sha256(secret, body) == expected


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

JOB_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client_and_mocks():
    """Spin up a tiny FastAPI app with the async_jobs router mounted and
    every DB / resume side-effect mocked so the test is pure."""
    app = FastAPI()
    app.include_router(async_jobs_router)
    client = TestClient(app)

    session = MagicMock()
    sessionlocal = MagicMock(return_value=session)

    with patch("app.api.async_jobs.SessionLocal", sessionlocal), \
         patch("app.api.async_jobs.finalize_terminal") as ft:
        yield client, session, ft


def _mock_job(
    session, *,
    status="submitted",
    webhook_auth="token",
    webhook_token="tkn",
    webhook_hmac_secret="s",
):
    job = MagicMock()
    job.id = JOB_ID
    job.status = status
    job.metadata_json = {
        "webhook_auth": webhook_auth,
        "webhook_token": webhook_token,
        "webhook_hmac_secret": webhook_hmac_secret,
    }
    session.query.return_value.filter_by.return_value.first.return_value = job
    return job


class TestEndpointTokenMode:
    def test_accepts_correct_token_and_finalizes(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="token", webhook_token="tkn")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=tkn",
            json={"status": "Complete", "id": 42},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "status": "completed"}
        ft.assert_called_once()

    def test_rejects_missing_token(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="token", webhook_token="tkn")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete",
            json={"status": "Complete"},
        )
        assert res.status_code == 401
        ft.assert_not_called()

    def test_rejects_wrong_token(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="token", webhook_token="tkn")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=nope",
            json={"status": "Complete"},
        )
        assert res.status_code == 401
        ft.assert_not_called()


class TestEndpointHmacMode:
    def test_accepts_correct_signature(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="hmac", webhook_hmac_secret="s")
        body = {"status": "Failure", "failureReason": "boom"}
        raw = json.dumps(body).encode()
        sig = "sha256=" + hmac.new(b"s", raw, sha256).hexdigest()

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-AE-Signature": sig,
            },
        )
        assert res.status_code == 200
        assert res.json()["status"] == "failed"
        ft.assert_called_once()

    def test_rejects_signature_over_different_body(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="hmac", webhook_hmac_secret="s")
        # Sign body A, but send body B
        sig = "sha256=" + hmac.new(b"s", b'{"status":"Complete"}', sha256).hexdigest()

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete",
            content=b'{"status":"Failure"}',
            headers={
                "Content-Type": "application/json",
                "X-AE-Signature": sig,
            },
        )
        assert res.status_code == 401
        ft.assert_not_called()

    def test_rejects_missing_signature(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="hmac")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete",
            json={"status": "Complete"},
        )
        assert res.status_code == 401


class TestEndpointBothMode:
    def test_accepts_when_only_token_present(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="both", webhook_token="tkn",
                  webhook_hmac_secret="s")
        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=tkn",
            json={"status": "Complete"},
        )
        assert res.status_code == 200
        ft.assert_called_once()

    def test_accepts_when_only_hmac_present(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="both", webhook_token="tkn",
                  webhook_hmac_secret="s")
        raw = b'{"status":"Complete"}'
        sig = "sha256=" + hmac.new(b"s", raw, sha256).hexdigest()
        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete",
            content=raw,
            headers={"Content-Type": "application/json", "X-AE-Signature": sig},
        )
        assert res.status_code == 200
        ft.assert_called_once()

    def test_rejects_when_neither_present(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="both", webhook_token="tkn",
                  webhook_hmac_secret="s")
        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete",
            json={"status": "Complete"},
        )
        assert res.status_code == 401


class TestEndpointEdgeCases:
    def test_unknown_job_returns_401_not_404(self, client_and_mocks):
        """Don't leak job existence — 401 for every invalid call."""
        client, session, ft = client_and_mocks
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=tkn",
            json={"status": "Complete"},
        )
        assert res.status_code == 401
        ft.assert_not_called()

    def test_already_finalised_job_returns_idempotent_ok(self, client_and_mocks):
        client, session, ft = client_and_mocks
        _mock_job(session, status="completed", webhook_token="tkn")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=tkn",
            json={"status": "Complete"},
        )
        assert res.status_code == 200
        assert res.json()["note"] == "already finalised"
        ft.assert_not_called()   # no double-resume

    def test_non_terminal_ae_status_is_ignored(self, client_and_mocks):
        """Callback arrived too early (AE still Executing). Leave the
        row alone so Beat keeps polling; don't resume the parent."""
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="token", webhook_token="tkn")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=tkn",
            json={"status": "Executing"},
        )
        assert res.status_code == 200
        assert "non-terminal" in res.json().get("note", "")
        ft.assert_not_called()

    def test_diverted_callback_is_ignored(self, client_and_mocks):
        """Diverted is also non-terminal — same path as Executing."""
        client, session, ft = client_and_mocks
        _mock_job(session, webhook_auth="token", webhook_token="tkn")

        res = client.post(
            f"/api/v1/async-jobs/{JOB_ID}/complete?token=tkn",
            json={"status": "Diverted"},
        )
        assert res.status_code == 200
        ft.assert_not_called()
