# Tenant Policies

**ADMIN-01** moves three operational knobs off the process-global `ORCHESTRATOR_*` env vars and onto a per-tenant `tenant_policies` table. Operators can now set different limits for different tenants through the toolbar **Tenant Policy** dialog (sliders icon) — a free-tier tenant and an enterprise tenant no longer have to share the same execution quota.

This page is the canonical reference. Read §4 before assuming every env knob is moveable — it isn't, and intentionally so.

> **One-line summary.** Three ADMIN-01 knobs + two ADMIN-02 rate-limit knobs + two SMART-XX feature flags are per-tenant with env fallback. LLM provider keys (ADMIN-03) live on the separate `tenant_secrets` vault — see §4 for the full carve-out rationale.

---

## 1. What ships today

| Knob | Before | After | Call site |
|---|---|---|---|
| `execution_quota_per_hour` | Env only (`ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR`) | Per-tenant, env fallback | `security/rate_limiter.py::_check_via_redis` + `_check_via_db` |
| `max_snapshots` | Env only (`ORCHESTRATOR_MAX_SNAPSHOTS`) | Per-tenant, env fallback | `workers/scheduler.py::prune_old_snapshots` (resolved once per tenant per run, not per workflow) |
| `mcp_pool_size` | Env only (`ORCHESTRATOR_MCP_POOL_SIZE`) | Per-tenant, env fallback. **Applies at pool construction** — existing pools keep their original size until `shutdown_pool()` or app restart. | `engine/mcp_client.py::_pool_for` |
| `rate_limit_requests_per_window` | Previously `ORCHESTRATOR_RATE_LIMIT_REQUESTS` *(but inert — see note below)* | **Now actually enforced**, per-tenant, env fallback | `security/tenant_rate_limit.py::TenantRateLimitMiddleware` (ADMIN-02) |
| `rate_limit_window_seconds` | New — replaces the old `ORCHESTRATOR_RATE_LIMIT_WINDOW` string ("1 minute") | Per-tenant, env fallback | Same middleware |
| `smart_04_lints_enabled` | — | **Default TRUE.** SMART-04 proactive authoring lints for the copilot. Cost-conscious tenants flip off to skip the lint pass (schema validation still runs). | `copilot/runner_tools.check_draft` |
| `smart_06_mcp_discovery_enabled` | — | **Default TRUE.** SMART-06 MCP tool discovery path. Off = the `discover_mcp_tools` runner tool returns `{discovery_enabled: false, tools: []}` and the system prompt skips MCP suggestions in narration. | `copilot/runner_tools.discover_mcp_tools` |
| `smart_02_pattern_library_enabled` | — | **Default TRUE.** SMART-02 accepted-patterns library. Off = `/promote` skips the pattern save and `recall_patterns` returns `{enabled: false, patterns: []}`. Agent falls back to synthesising without tenant-specific few-shot. | `copilot/pattern_library.save_accepted_pattern` + `recall_patterns` |
| `smart_01_scenario_memory_enabled` | — | **Default FALSE (opt-in).** SMART-01 scenario memory. When on, every successful `execute_draft` auto-saves a scenario named `auto-<hash>` deduped by payload hash (no `expected_output_contains` — just "this payload still runs"). Cost: one INSERT per successful run. | `copilot/runner_tools.execute_draft_sync` → `_auto_save_scenario_from_run` |
| `smart_01_strict_promote_gate_enabled` | — | **Default FALSE (opt-in).** SMART-01 strict promote-gate. When on, `/api/v1/copilot/drafts/{id}/promote` runs every saved scenario and refuses with HTTP 400 on any non-pass result (no "promote anyway" override). Cost: one full draft execution per scenario at promote time. Off = the PromoteDialog's existing soft gate still shows pass/fail badges and allows override. | `api/copilot_drafts.promote_draft` → `_enforce_strict_scenario_gate` |
| `smart_05_vector_docs_enabled` | — | **Default FALSE (opt-in).** SMART-05 vector-backed docs search. When on, `search_docs` embeds the copilot docs corpus (~30–50 chunks) via the process-wide `smart_05_embedding_provider` / `smart_05_embedding_model` pair and ranks queries by cosine similarity; on any embedding failure the call auto-falls back to word-overlap with a `vector_fallback` hint, so enabling this never returns *fewer* results than off. Cost: one corpus embed per process restart + one query embed per copilot search. | `copilot/docs_index.search_docs` + `get_node_examples` |

> **Historical note:** before ADMIN-02 the `slowapi.Limiter` was instantiated with `default_limits` but no middleware was ever installed to apply it. The rate-limit env vars were effectively dead config. ADMIN-02's `TenantRateLimitMiddleware` is the first real enforcement. The old `ORCHESTRATOR_RATE_LIMIT_WINDOW` string setting is deprecated — the new `ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS` int supersedes it.

---

## 2. Schema + API

### Table (migration `0020`)

```
tenant_policies:
  tenant_id                         VARCHAR(64) PRIMARY KEY
  execution_quota_per_hour          INTEGER NULL   — null = use env default   (0020)
  max_snapshots                     INTEGER NULL                             (0020)
  mcp_pool_size                     INTEGER NULL                             (0020)
  rate_limit_requests_per_window    INTEGER NULL                             (0021)
  rate_limit_window_seconds         INTEGER NULL                             (0021)
  smart_04_lints_enabled            BOOLEAN NOT NULL DEFAULT TRUE            (0024)
  smart_06_mcp_discovery_enabled    BOOLEAN NOT NULL DEFAULT TRUE            (0025)
  smart_02_pattern_library_enabled  BOOLEAN NOT NULL DEFAULT TRUE            (0026)
  smart_01_scenario_memory_enabled  BOOLEAN NOT NULL DEFAULT FALSE           (0028)
  smart_01_strict_promote_gate_enabled BOOLEAN NOT NULL DEFAULT FALSE        (0028)
  smart_05_vector_docs_enabled      BOOLEAN NOT NULL DEFAULT FALSE           (0029)
  created_at, updated_at
```

Integer knobs are nullable-for-env-fallback (per-field, see §3). Boolean SMART-XX flags are NOT NULL with a design-default matching the feature's expected cost (on for zero-LLM-cost features, off for features that incur net-new spend).

Single row per tenant (not multiple labeled rows like MCP servers or integrations). RLS enabled with the standard `current_setting('app.tenant_id')` policy.

### Endpoints — singleton pattern

```http
GET  /api/v1/tenant-policy
PATCH /api/v1/tenant-policy
```

Response shape:

```json
{
  "tenant_id": "acme",
  "values": {
    "execution_quota_per_hour": 500,
    "max_snapshots": 20,
    "mcp_pool_size": 4,
    "rate_limit_requests_per_window": 100,
    "rate_limit_window_seconds": 60
  },
  "flags": {
    "smart_04_lints_enabled": true,
    "smart_06_mcp_discovery_enabled": true,
    "smart_02_pattern_library_enabled": true,
    "smart_01_scenario_memory_enabled": false,
    "smart_01_strict_promote_gate_enabled": false,
    "smart_05_vector_docs_enabled": false
  },
  "source": {
    "execution_quota_per_hour": "tenant_policy",
    "max_snapshots": "env_default",
    "mcp_pool_size": "env_default",
    "rate_limit_requests_per_window": "env_default",
    "rate_limit_window_seconds": "env_default",
    "smart_04_lints_enabled": "env_default",
    "smart_06_mcp_discovery_enabled": "env_default",
    "smart_02_pattern_library_enabled": "env_default",
    "smart_01_scenario_memory_enabled": "env_default",
    "smart_01_strict_promote_gate_enabled": "env_default",
    "smart_05_vector_docs_enabled": "env_default"
  },
  "updated_at": "2026-04-22T12:34:56+00:00"
}
```

`values` carries integer knobs (quotas, limits, pool sizes). `flags` carries SMART-XX booleans — separated so the frontend can render typed toggles without switching on schema per key. `source` names where each field came from for both `values` and `flags` so the UI can show "overridden" vs. "inherited" badges.

### PATCH tri-state semantics

The PATCH body uses three states per field, distinguished via Pydantic's `model_fields_set`:

| Client sends | Server effect |
|---|---|
| Field omitted | Leave the prior override alone |
| Field explicit `null` | Clear the override — falls through to env default |
| Field integer | Set / overwrite the override |

So a body like `{"execution_quota_per_hour": null}` means "reset this one knob" without touching the other two.

---

## 3. Resolver + precedence

`engine/tenant_policy_resolver.get_effective_policy(tenant_id)` is the single read path. Every call site — `check_execution_quota`, `prune_old_snapshots`, `_pool_for` — goes through it.

Precedence per field, highest first:

1. **`tenant_policies` row** for this tenant, where the column is **non-null**.
2. **`settings.<knob>` env default**.

Nulls in the DB fall through to step 2 on a *per-field* basis — a row that only sets `execution_quota_per_hour` leaves the other two at env defaults. This is why the columns are nullable, not zero-as-sentinel.

### Graceful degradation on DB errors

If reading `tenant_policies` fails for any reason (connection refused, missing table, RLS denial, schema drift), the resolver logs a warning and returns the env defaults. Rationale: quota enforcement is a hot path — a flaky `tenant_policies` read must not 500 every `/execute` call. The production-safe behaviour is to fall back to the process-global defaults and keep serving.

### Call cadence

Reads are one indexed primary-key lookup per call. Every call site is already low-frequency:

- **Quota check** — once per `/execute` (plus one for sync/async branches).
- **MCP pool** — once per `(tenant, server)` combo per process lifetime (the pool dict caches).
- **Snapshot prune** — once per tenant per day (the task de-dupes tenants across workflows).

No resolver-level caching is in place; changes take effect immediately from the admin UI's perspective. If a future hot path starts calling the resolver tens of thousands of times per second, add a short TTL cache inside the resolver — don't refactor call sites.

---

## 4. Scope caveats — what ADMIN-01 does NOT move

The "bring `.env` configs to the admin UI" question in general is a minefield of footguns. ADMIN-01 only moves the three knobs listed in §1. Here's why the others stay behind, grouped by the reason:

### 4a. Group: infra bootstrap — can't be admin-UI config

| Env var | Why not |
|---|---|
| `ORCHESTRATOR_DATABASE_URL` | The admin UI needs the DB to read its own config. Chicken and egg. |
| `ORCHESTRATOR_REDIS_URL` | Celery broker / PKCE state. Required before the UI can authenticate anyone. |
| `ORCHESTRATOR_SECRET_KEY` | Signs the JWTs the admin UI uses. Rotating it through the UI logs everyone out including the rotator. |
| `ORCHESTRATOR_VAULT_KEY` | Fernet key for every other secret. Belongs in a proper secret manager (KMS / Vault / Doppler), not an app DB. |
| `ORCHESTRATOR_AUTH_MODE` / `ORCHESTRATOR_OIDC_*` | Changing these mid-flight reshapes the auth model — risk of locking every operator out. |
| `ORCHESTRATOR_CORS_ORIGINS` | Wrong value = frontend can't reach backend, including the admin UI itself. |
| `ORCHESTRATOR_USE_CELERY` | Changes whether there's a worker to do anything. Restart-required. |

### 4b. Group: app-level knobs that change rarely and should NOT be runtime-editable

| Env var | Why not |
|---|---|
| `ORCHESTRATOR_EMBEDDING_DEFAULT_PROVIDER` / `_MODEL` | Changing mid-ingestion produces chunks with inconsistent vectors — a subtle data-corruption risk without a full backfill. Env-var-only is protective. |
| `ORCHESTRATOR_KB_MAX_FILE_SIZE_MB` | Rare change; restart is fine. |
| `ORCHESTRATOR_FAISS_INDEX_DIR` | Filesystem path; nonsensical to change at runtime. |
| `LANGFUSE_*` | Observability plumbing. Set once per deploy. |

### 4c. Group: deliberately carved out as separate tickets

| Env vars | Why separate / status |
|---|---|
| `ORCHESTRATOR_RATE_LIMIT_REQUESTS` / `_WINDOW_SECONDS` | **Shipped as ADMIN-02.** Original deferral was because `slowapi` read its limit string at import time. Investigation showed slowapi was never actually wired into a middleware — the env vars had no runtime effect. ADMIN-02 dropped the slowapi path entirely in favour of a tiny custom `TenantRateLimitMiddleware` that does Redis INCR+EXPIRE per-tenant. |
| Provider API keys (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_BASE_URL`) | **Shipped as ADMIN-03.** Stored in the existing Fernet-encrypted `tenant_secrets` vault under well-known names (`LLM_GOOGLE_API_KEY`, `LLM_OPENAI_API_KEY`, `LLM_OPENAI_BASE_URL`, `LLM_ANTHROPIC_API_KEY`). `engine/llm_credentials_resolver` threads tenant_id into seven call sites (`_call_google/_call_openai/_call_anthropic` + three stream variants + two ReAct handlers). Dialog lives behind the toolbar Key icon. Embedding paths (`_embed_google`, `_embed_openai`) still use the shared env keys — threading tenant_id through ingestor/retriever is a separate refactor; follow-up if needed. |

### 4d. Already solved via dedicated tables

| What | Where |
|---|---|
| Per-tenant MCP server URL + auth | `tenant_mcp_servers` (MCP-02) + `McpServersDialog.tsx` |
| Per-tenant AutomationEdge connection config | `tenant_integrations(system='automationedge')` + `IntegrationsDialog.tsx` |
| Per-tenant Vertex project + location | `tenant_integrations(system='vertex')` + `VertexProjectsDialog.tsx` |
| Per-tenant secrets (Fernet-encrypted) | `tenant_secrets` + `SecretsDialog.tsx` |

---

## 5. UI semantics

Toolbar → **SlidersHorizontal** icon opens `TenantPolicyDialog`. One form, three rows — one per knob. Each row shows:

- **Label + description** of the knob.
- **A source pill** — `override` (blue) when the tenant has a tenant_policies override, or `env default` (grey) when inherited. Pills change to `pending override` / `pending reset` (yellow) while unsaved.
- **A number input** pre-populated with the current override value (or blank with the env default in the placeholder when inherited).
- **A "reset" button** (visible only when the field is currently overridden) that clears the override on save.
- **A "undo" button** (visible only when the field has a pending change) that cancels that pending change without touching the server.

Save button is disabled until there's at least one pending change. **Discard** clears all pending changes and leaves the server state untouched.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Quota check returns 429 at a lower number than the dialog shows | Dialog shows effective values, but the row was recently changed and the Redis counter hasn't rolled over — bucket is still the old quota for the remaining slot of the hour | Wait for the hour bucket, or flush `orch:quota:<tenant>:*` |
| Snapshot prune still keeps too many | Policy value is `0` (unlimited), not a cap | `0` means unlimited — set a positive integer |
| MCP pool didn't resize after override | Existing pool was built with the old size; resolver only runs at pool construction | Restart the process OR wait for that (tenant, server) pool to get evicted (not automatic today) |
| `tenant_policy_resolver: failed to read … falling back to env defaults` in logs | DB was unreachable or the migration hadn't run | Apply migration `0020`; confirm the app role has `SELECT, INSERT, UPDATE` on `tenant_policies` |
| Dialog opens but all fields show `env default` even after Save | Save hit a different tenant (OIDC or `X-Tenant-Id` mismatch) | Confirm the tenant_id the UI is authenticated as matches the one you expect |

---

## 7. Related reading

* [Security](security.md) — RLS on `tenant_policies`, rate limiting
* [Setup Guide](../SETUP_GUIDE.md) — §7.1 env table marks these three knobs as **fallbacks**
* [API Reference](api-reference.md) — `tenant-policy` singleton endpoint shape
* [Vertex AI Integration](vertex.md) — analogous pattern (per-tenant routing, env fallback) for a different knob
* [MCP Audit](mcp-audit.md) — the MCP-02 registry is the precedent this dialog learned from
