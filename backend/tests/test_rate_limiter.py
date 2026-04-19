"""Unit tests for the atomic execution-quota counter.

These tests exercise the Redis path via a hand-rolled thread-safe fake so
concurrent INCR behaviour is verifiable without needing a live Redis.
"""

from __future__ import annotations

import threading

import pytest

from app.security import rate_limiter
from app.security.rate_limiter import (
    _check_via_redis,
    _redis_quota_key,
)


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
    """Thread-safe in-memory Redis stand-in for the two ops we use."""

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


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    yield r


@pytest.fixture
def under_quota(monkeypatch):
    """Pin the quota to 5 for the duration of a test."""
    monkeypatch.setattr(rate_limiter.settings, "execution_quota_per_hour", 5)


class TestRedisQuotaPath:
    def test_allows_under_limit(self, fake_redis, under_quota):
        for i in range(5):
            n = _check_via_redis("t1", client_factory=lambda: fake_redis)
            assert n == i + 1

    def test_rejects_at_limit(self, fake_redis, under_quota):
        # Consume the 5 allowed calls
        for _ in range(5):
            _check_via_redis("t1", client_factory=lambda: fake_redis)
        # The 6th must 429
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _check_via_redis("t1", client_factory=lambda: fake_redis)
        assert exc.value.status_code == 429
        assert "6/5" in exc.value.detail

    def test_separate_tenants_have_separate_buckets(self, fake_redis, under_quota):
        for _ in range(5):
            _check_via_redis("t1", client_factory=lambda: fake_redis)
        # t2 can still go
        n = _check_via_redis("t2", client_factory=lambda: fake_redis)
        assert n == 1

    def test_concurrent_callers_cannot_exceed_quota(self, fake_redis, under_quota):
        """The core TOCTOU fix — 20 threads racing should land exactly 5
        successes and 15 HTTP 429s, never 6+ successes."""
        successes: list[int] = []
        failures: list[int] = []
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()  # release all threads at once
            try:
                _check_via_redis("t1", client_factory=lambda: fake_redis)
                successes.append(1)
            except Exception:
                failures.append(1)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 5, (
            f"expected exactly 5 successes under quota=5, got {len(successes)}"
        )
        assert len(failures) == 15


class TestRedisKeyShape:
    def test_key_includes_tenant_and_hour_bucket(self):
        k = _redis_quota_key("alpha", "2026-04-19-12")
        assert k == "orch:quota:alpha:2026-04-19-12"

    def test_default_key_uses_current_hour(self):
        k1 = _redis_quota_key("alpha")
        # Shape: orch:quota:<tenant>:YYYY-MM-DD-HH
        parts = k1.split(":")
        assert parts[0] == "orch"
        assert parts[1] == "quota"
        assert parts[2] == "alpha"
        assert len(parts[3].split("-")) == 4


class TestDbFallbackPathIntegration:
    def test_db_fallback_invoked_when_redis_factory_raises(self, monkeypatch):
        """If the Redis pipeline blows up, check_execution_quota must
        fall through to the DB-count path and not raise."""
        from unittest.mock import MagicMock

        def boom():
            raise RuntimeError("redis is down")

        monkeypatch.setattr(rate_limiter, "_get_redis_client", boom)

        # Mock the DB path to return 0 so the overall call succeeds.
        fake_db = MagicMock()
        fake_db.query.return_value.filter.return_value.count.return_value = 0
        # No raise expected.
        rate_limiter.check_execution_quota(fake_db, "t1")
