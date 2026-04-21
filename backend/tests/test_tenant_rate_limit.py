"""ADMIN-02 — tests for the per-tenant API rate-limit middleware.

Covers:

* ``check_api_rate_limit`` atomic INCR+EXPIRE semantics.
* Bucket keying: requests within the same window share a counter;
  the next window starts fresh.
* Separate tenants have independent buckets.
* ``TenantRateLimitMiddleware`` routing: 429 when over limit,
  pass-through when under, fail-open when Redis breaks.
* Exempt paths (`/health`, `/docs`) are never rate-limited.

Uses the same thread-safe fake-Redis pattern as ``test_rate_limiter``
so we can assert atomicity without a live Redis.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake Redis — thread-safe INCR + EXPIRE + pipeline, nothing else.
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, parent: "_FakeRedis"):
        self._parent = parent
        self._ops: list = []

    def incr(self, key: str):
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int):
        self._ops.append(("expire", key, seconds))
        return self

    def execute(self) -> list:
        return [self._parent._apply(op) for op in self._ops]


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, int] = {}
        self._lock = threading.Lock()

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def _apply(self, op):
        with self._lock:
            if op[0] == "incr":
                _, key = op
                self._store[key] = self._store.get(key, 0) + 1
                return self._store[key]
            if op[0] == "expire":
                return True
            raise AssertionError(f"unsupported op: {op[0]}")


# ---------------------------------------------------------------------------
# check_api_rate_limit — low-level INCR path
# ---------------------------------------------------------------------------


class TestCheckApiRateLimit:
    def test_allows_under_limit(self):
        from app.security.tenant_rate_limit import check_api_rate_limit

        r = _FakeRedis()
        for i in range(5):
            n = check_api_rate_limit(
                "t1",
                requests_per_window=5,
                window_seconds=60,
                client_factory=lambda: r,
                now=1_000_000,
            )
            assert n == i + 1

    def test_rejects_over_limit(self):
        from app.security.tenant_rate_limit import (
            _RateLimitExceeded,
            check_api_rate_limit,
        )

        r = _FakeRedis()
        for _ in range(5):
            check_api_rate_limit(
                "t1", requests_per_window=5, window_seconds=60,
                client_factory=lambda: r, now=1_000_000,
            )
        with pytest.raises(_RateLimitExceeded) as exc:
            check_api_rate_limit(
                "t1", requests_per_window=5, window_seconds=60,
                client_factory=lambda: r, now=1_000_000,
            )
        assert exc.value.count == 6
        assert exc.value.limit == 5
        assert exc.value.window_seconds == 60

    def test_next_window_resets_counter(self):
        from app.security.tenant_rate_limit import check_api_rate_limit

        r = _FakeRedis()
        # Fill window 1.
        for _ in range(5):
            check_api_rate_limit(
                "t1", requests_per_window=5, window_seconds=60,
                client_factory=lambda: r, now=1_000_000,
            )
        # Jump to the next window (60s later) — counter is a new bucket.
        n = check_api_rate_limit(
            "t1", requests_per_window=5, window_seconds=60,
            client_factory=lambda: r, now=1_000_060,
        )
        assert n == 1

    def test_separate_tenants_have_independent_buckets(self):
        from app.security.tenant_rate_limit import check_api_rate_limit

        r = _FakeRedis()
        for _ in range(5):
            check_api_rate_limit(
                "t1", requests_per_window=5, window_seconds=60,
                client_factory=lambda: r, now=1_000_000,
            )
        # Tenant 2 shares the same fake redis but a different bucket key.
        n = check_api_rate_limit(
            "t2", requests_per_window=5, window_seconds=60,
            client_factory=lambda: r, now=1_000_000,
        )
        assert n == 1

    def test_concurrent_callers_cannot_exceed_limit(self):
        """Atomicity check: 20 threads racing for 5 slots => exactly
        5 successes, 15 RateLimitExceeded raises — never 6+ through."""
        from app.security.tenant_rate_limit import (
            _RateLimitExceeded,
            check_api_rate_limit,
        )

        r = _FakeRedis()
        successes: list[int] = []
        failures: list[int] = []
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            try:
                check_api_rate_limit(
                    "t1", requests_per_window=5, window_seconds=60,
                    client_factory=lambda: r, now=1_000_000,
                )
                successes.append(1)
            except _RateLimitExceeded:
                failures.append(1)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 5
        assert len(failures) == 15


# ---------------------------------------------------------------------------
# Middleware — end-to-end through a minimal FastAPI app
# ---------------------------------------------------------------------------


def _make_app(fake_redis: _FakeRedis) -> FastAPI:
    from app.security.tenant_rate_limit import TenantRateLimitMiddleware

    app = FastAPI()
    app.add_middleware(TenantRateLimitMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    # Patch the module-local Redis client getter so every call to the
    # resolver-plus-middleware chain uses our fake.
    from app.security import rate_limiter

    rate_limiter._redis_client = fake_redis
    return app


class TestMiddlewareEndToEnd:
    def test_under_limit_passes_through(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "rate_limit_requests", 5)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

        r = _FakeRedis()
        app = _make_app(r)
        client = TestClient(app)

        for _ in range(5):
            res = client.get("/ping", headers={"X-Tenant-Id": "t1"})
            assert res.status_code == 200

    def test_over_limit_returns_429_with_retry_after(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "rate_limit_requests", 3)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 120)

        r = _FakeRedis()
        app = _make_app(r)
        client = TestClient(app)

        for _ in range(3):
            client.get("/ping", headers={"X-Tenant-Id": "t1"})

        res = client.get("/ping", headers={"X-Tenant-Id": "t1"})
        assert res.status_code == 429
        assert res.headers.get("retry-after") == "120"
        body = res.json()
        assert "API rate limit exceeded" in body["detail"]

    def test_exempt_paths_never_counted(self, monkeypatch):
        """Hitting /health many times must not consume the /ping budget."""
        from app.config import settings
        monkeypatch.setattr(settings, "rate_limit_requests", 2)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

        r = _FakeRedis()
        app = _make_app(r)
        client = TestClient(app)

        # Exempt bursts first.
        for _ in range(20):
            assert client.get("/health").status_code == 200
        # The tenant's budget is still untouched — they can burn their 2.
        for _ in range(2):
            assert client.get("/ping", headers={"X-Tenant-Id": "t1"}).status_code == 200
        # Third one is rate-limited.
        assert client.get("/ping", headers={"X-Tenant-Id": "t1"}).status_code == 429

    def test_fail_open_on_redis_error(self, monkeypatch):
        """A broken Redis must NOT hard-fail every request."""
        from app.config import settings
        monkeypatch.setattr(settings, "rate_limit_requests", 1)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

        def _explode():
            raise RuntimeError("redis down")

        from app.security import tenant_rate_limit
        from app.security.tenant_rate_limit import TenantRateLimitMiddleware

        app = FastAPI()
        app.add_middleware(TenantRateLimitMiddleware)

        @app.get("/ping")
        def ping():
            return {"ok": True}

        with patch.object(tenant_rate_limit, "check_api_rate_limit", side_effect=_explode):
            client = TestClient(app)
            for _ in range(5):
                # Every request would normally be 429 (limit=1); with
                # Redis broken the middleware fails open.
                assert client.get("/ping", headers={"X-Tenant-Id": "t1"}).status_code == 200
