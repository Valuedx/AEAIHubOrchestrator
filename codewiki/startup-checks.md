# Startup Checks & Readiness

**STARTUP-01** adds a preflight pass that runs at app boot and on every `/health/ready` call. Each check reports one of three levels — `pass`, `warn`, `fail` — with a **specific remediation string** pointing at the exact fix. Results surface in three places:

1. **Uvicorn logs** (INFO/WARNING/ERROR) — the one-line summary per check at boot.
2. **`/health/ready`** — structured JSON with the full per-check detail. Returns **503** when any check is `fail`, **200** otherwise (warns included).
3. **Red/amber banner in the UI** — fetched on first load by `StartupHealthBanner`. Rendered above the workflow banner so it's the first thing an operator sees when they land.

This page is the canonical reference for what each check does, when it fires, and how to remedy a failure.

---

## 1. Check inventory

### Tier 1 — always on

| Check | What it verifies | Failure level | Remedy |
|---|---|---|---|
| `database` | `SELECT 1` + alembic head match | `fail` on connectivity loss; `warn` when schema is behind head | Run `alembic upgrade head` from `backend/` |
| `redis` | Redis `PING` | `fail` when `USE_CELERY=true`; `warn` otherwise | Start Redis; confirm `ORCHESTRATOR_REDIS_URL` |
| `celery_workers` | `inspect().ping()` returns ≥ 1 worker when `USE_CELERY=true` | `warn` | Start a worker: `celery -A app.workers.celery_app worker --loglevel=info` |
| `rls_posture` | App DB role is NOT a superuser | `warn` | Create a non-superuser role per `SETUP_GUIDE §5.2a` and repoint `DATABASE_URL` |
| `auth_mode` | `AUTH_MODE=jwt` has real `SECRET_KEY`; OIDC has all required fields when enabled | `warn` on placeholder secret; `fail` on missing OIDC fields | Set `SECRET_KEY`; fill every `OIDC_*` env var |
| `vault_key` | `VAULT_KEY` present when `tenant_secrets` has rows | `fail` when rows exist but key blank; `warn` otherwise | Generate Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Tier 2 — bounded probes, still fast

| Check | What it verifies | Failure level | Skipped when |
|---|---|---|---|
| `mcp_default_server` | TCP connect to `ORCHESTRATOR_MCP_SERVER_URL` | `warn` | Any tenant has a `tenant_mcp_servers` row (migrated to MCP-02 registry) |

### Not here (by design)

* **Per-node workflow validation** — already runs at save time via `config_validator`. Startup checks don't re-scan graph JSON.
* **LLM provider key presence** — fails with a clear message on first call; adding it here adds boot-time latency for a rare misconfiguration that surfaces loudly anyway.
* **External HTTP targets in every workflow** — combinatorial, brittle, would make a flaky network partition 503 the whole app. Out of scope.

---

## 2. How checks fire

### At boot (lifespan pass)

The FastAPI `lifespan` handler in `main.py` runs every check once and logs the outcome at the matching level. Log format:

```
2026-04-21 09:15:02 [INFO] app.startup_checks: startup check database pass: Connected and on head revision 0021.
2026-04-21 09:15:03 [WARNING] app.startup_checks: startup check celery_workers WARN: No Celery workers responded to ping within 2s.  remediation=Start a worker: `celery -A app.workers.celery_app worker --loglevel=info`. Workflows dispatched via Celery will queue indefinitely until one is running.
2026-04-21 09:15:03 [WARNING] __main__: Startup checks report WARN — app will serve traffic; see remediation messages above.
```

Boot does NOT fail on `fail` results — the app still serves traffic, but `/health/ready` will 503 until the failure is remedied. This keeps rolling upgrades safe: a single misconfigured replica doesn't take down the whole deploy.

### Gating the pass during tests

The lifespan is gated by `ORCHESTRATOR_SKIP_STARTUP_CHECKS: bool = False`. The test harness (`backend/tests/conftest.py`) sets it to `true` so `TestClient(app)` doesn't pound a real DB / Redis per test. Tests that exercise startup checks (`tests/test_startup_checks.py`) import the functions directly.

### On demand (`/health/ready`)

```bash
curl http://localhost:8001/health/ready | jq
```

```json
{
  "status": "warn",
  "checks": [
    {
      "name": "database",
      "status": "pass",
      "message": "Connected and on head revision 0021.",
      "remediation": "",
      "details": {"revision": "0021"}
    },
    {
      "name": "celery_workers",
      "status": "warn",
      "message": "No Celery workers responded to ping within 2s.",
      "remediation": "Start a worker: `celery -A app.workers.celery_app worker --loglevel=info`. Workflows dispatched via Celery will queue indefinitely until one is running.",
      "details": {}
    },
    ...
  ]
}
```

Runs live each call — no caching. This is intentional: the resolver-tier checks (DB, Redis, Celery) all return in under 100ms when healthy. A probe that doesn't reflect *current* state isn't useful for k8s readiness.

### From the UI (`StartupHealthBanner`)

Component in `frontend/src/components/banner/StartupHealthBanner.tsx` calls `api.getHealthReady()` once on mount:

* **All `pass`** → no banner rendered.
* **Any `warn`** → amber banner with per-check detail. **Dismissible** for 1 hour (sticky in `localStorage`), so a tolerated warn (e.g. dev-mode RLS posture) doesn't nag all day.
* **Any `fail`** → red banner with `role="alert"`. **Non-dismissible** — a failing readiness probe is the orchestrator announcing it can't serve properly; hiding that is wrong.

The banner is collapsed by default (check names only) with an expand chevron that reveals the full `message` + `remediation` per check.

---

## 3. Operational runbook

### k8s liveness + readiness

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8001
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8001
  periodSeconds: 15
  failureThreshold: 2
  timeoutSeconds: 5
```

`/health` is always 200 while the process is up; cycles a pod only on real crashes. `/health/ready` cycles a pod out of the Service pool when it can't serve properly (DB down, OIDC misconfigured) without killing it outright — gives k8s a chance to route traffic away while ops investigate.

### First-boot of a fresh deploy

Expected warns right after `docker compose up -d`:

1. `celery_workers` — **warn** until you start a worker (or set `USE_CELERY=false`).
2. `rls_posture` — **warn** if `DATABASE_URL` points at `postgres` superuser (default for local dev).
3. `vault_key` — **warn** if `VAULT_KEY` is blank AND `tenant_secrets` is empty (benign until you create your first secret).

None of these are `fail`; they're all "deploy looks like dev, harden before prod."

### Interpreting sudden post-deploy regressions

If a deploy that used to be green starts warning `database` with "Schema behind head," someone landed a migration but the deploy pipeline didn't run `alembic upgrade head`. That's the top reason for the cryptic `column does not exist` errors you'd otherwise hit on first tenant request.

---

## 4. Extending the check registry

1. Add a new function to `backend/app/startup_checks.py` that returns a `CheckResult`. Catch your own exceptions — `run_all_checks` will also wrap uncaught raises as synthetic `fail` results, but a specific, remedy-bearing message from inside the check is better.
2. Append it to the `_REGISTRY` tuple.
3. Add a unit test in `tests/test_startup_checks.py` proving `pass` / `warn` / `fail` branches via mocked dependencies.
4. Document it in the table in §1 above.

Every new check should answer four questions up front:

* **What dependency is this verifying?**
* **What does a `fail` look like in practice — an outage, a silent corruption, a misconfig?**
* **Why is it `fail` vs. `warn`?** (Can the app still serve traffic while this is broken?)
* **What's the exact shell command / env var to fix it?**

If any of those are fuzzy, the check isn't ready to ship.

---

## 5. Related reading

* [Tenant Policies](tenant-policies.md) — ADMIN-01 + ADMIN-02 runtime knobs the startup checks overlap with (quota, rate limits)
* [Security](security.md) — RLS design the `rls_posture` check guards
* [Setup Guide](../SETUP_GUIDE.md) §5.2a — the non-superuser role setup `rls_posture` nudges operators to
* [Deployment](deployment.md) — k8s probe configuration
