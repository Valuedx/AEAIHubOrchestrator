# Security

---

## Authentication modes

Controlled by `ORCHESTRATOR_AUTH_MODE`. The `get_tenant_id` dependency (from `backend/app/security/tenant.py`) is injected into all protected API routes.

### Dev mode (`auth_mode: dev`)

For local development. Tenant ID is extracted from:

1. `X-Tenant-Id` HTTP header (primary)
2. `x_tenant_id` query parameter (fallback)

If neither is present, returns **401**. The frontend sends `X-Tenant-Id` from `VITE_TENANT_ID` (default `"default"`).

A convenience endpoint `GET /auth/token?tenant_id=<id>` is available in dev mode to generate signed JWTs for testing.

### JWT mode (`auth_mode: jwt`)

For production. Every request must include an `Authorization: Bearer <token>` header with a valid HS256 JWT containing a `tenant_id` claim.

**Token structure:**

| Claim | Description |
|-------|-------------|
| `sub` | Subject (default `"api"`) |
| `tenant_id` | Tenant identifier |
| `iat` | Issued at |
| `exp` | Expiry (24 hours from issuance) |

Tokens are signed with `ORCHESTRATOR_SECRET_KEY` using HS256. Invalid or expired tokens return **401**.

### OIDC mode (optional)

When `ORCHESTRATOR_OIDC_ENABLED=true`, the backend exposes an `/auth/oidc` router for OAuth 2.0 Authorization Code flow.

| Setting | Description |
|---------|-------------|
| `oidc_issuer` | OIDC provider URL (e.g. `https://accounts.google.com`) |
| `oidc_client_id` | OAuth client ID |
| `oidc_client_secret` | OAuth client secret |
| `oidc_redirect_uri` | Callback URL |
| `oidc_tenant_claim` | ID token claim used as `tenant_id` (default: `email`) |
| `oidc_scopes` | OAuth scopes (default: `openid email profile`) |

The frontend (`VITE_AUTH_MODE=oidc`) shows a `LoginPage` and stores the access token in `sessionStorage` as `ae_access_token` — tab-scoped so the token doesn't survive a browser restart.

### Local password mode (`auth_mode: local`)

Username + password against a local `users` table. For deployments without an identity provider or where operators want the orchestrator to own credentials directly. Issues the same HS256 JWT as `jwt` mode, so every downstream endpoint is unchanged.

**Routes (only mounted when `auth_mode=local`):**

- `POST /auth/local/login` — body `{tenant_id, username, password}` → `{access_token, token_type: "bearer", user: {...}}`
- `GET /auth/me` (any JWT-issuing mode) — returns the caller's `users` row based on the token's `sub` claim.
- `POST /api/v1/users` (admin) — create a user.
- `GET /api/v1/users` (admin) — list users within the caller's tenant.
- `PUT /api/v1/users/{id}/password` (admin) — reset password.
- `PUT /api/v1/users/{id}/disabled` (admin) — toggle disabled. Self-disable is refused — the only path back from a fully-locked tenant is a DB fix.
- `DELETE /api/v1/users/{id}` (admin) — delete. Self-delete is refused.

**Storage:**

- `users` table: `id` (UUID PK), `tenant_id`, `username`, `email`, `password_hash`, `is_admin`, `disabled`, timestamps, `last_login_at`.
- Username is case-insensitive within a tenant (unique index on `(tenant_id, lower(username))`). Two tenants can share a username.
- Passwords are hashed with **argon2id** (argon2-cffi default parameters — 64 MiB, 3 passes). Bad passwords / unknown users / disabled accounts all return the same generic 401 to defeat account enumeration.
- RLS is enabled and forced on `users` using the same `app.tenant_id` GUC pattern as every other tenant-scoped table.

**Password policy:**

Length only — minimum `ORCHESTRATOR_PASSWORD_MIN_LENGTH` characters (default 8). We deliberately skip complexity rules; they push users toward predictable substitutions without meaningfully improving security.

**Bootstrap admin:**

Set `ORCHESTRATOR_LOCAL_ADMIN_USERNAME` and `ORCHESTRATOR_LOCAL_ADMIN_PASSWORD` (optionally `ORCHESTRATOR_LOCAL_ADMIN_TENANT_ID`, default `"default"`). On first boot into `auth_mode=local` the lifespan hook creates an admin user with those credentials. Subsequent boots are no-ops; changing the env vars after the row exists has **no effect** — use the password-reset endpoint instead.

**Local Active Directory / LDAP binding:**

Explicitly **not** in this revision. When it ships, an `authenticate_external(...)` path will land next to `authenticate(...)` in `backend/app/security/local_auth.py`, routed through the same `POST /auth/local/login` endpoint so the frontend doesn't change. The `users` table already has room for an optional external-provider column without a breaking migration.

The frontend (`VITE_AUTH_MODE=local`) shows a tenant/username/password form on the `LoginPage` that POSTs to `/auth/local/login` and stores the returned JWT the same way OIDC mode does.

### A2A authentication

Agent-to-Agent requests use a separate auth mechanism:

- `POST /tenants/{tenant_id}/a2a` requires `Authorization: Bearer <raw_a2a_key>`.
- A2A keys are SHA-256 hashed before storage — the raw key is returned only at creation time.
- Keys are scoped per-tenant with unique labels.

See the [API Reference](api-reference.md) A2A section for key management endpoints.

---

## Multi-tenancy

Every resource in the system is scoped to a `tenant_id`. Isolation is enforced at two layers:

### Application layer

All database queries include a `tenant_id` filter. The `get_tenant_id` dependency ensures a valid tenant is identified before any data access.

### Database layer (Row Level Security)

PostgreSQL Row Level Security (RLS) policies provide defense-in-depth. Every request handler that reads or writes tenant-scoped tables takes its SQLAlchemy session via the `get_tenant_db` FastAPI dependency (defined in `backend/app/database.py`). That dependency resolves the tenant from the `X-Tenant-Id` header (or JWT claim) and calls `set_tenant_context(db, tenant_id)` on the session before yielding it, so the `app.tenant_id` GUC is in place before any query runs:

```sql
SELECT set_tenant_id('<tenant_id>');  -- session-scoped, survives commits
```

Handlers that don't identify the tenant via the `X-Tenant-Id` header — notably the path-scoped A2A surface (`/tenants/{tenant_id}/...`) and the Celery worker's own `SessionLocal()` — keep using the plain `get_db` dependency but call `set_tenant_context(db, path_tenant_id)` themselves before any query.

> **Why this matters — incident 2026-04-21:** A normal `POST /api/v1/workflows` started failing with `InsufficientPrivilege: new row violates row-level security policy for table "workflow_definitions"` the day a tenant switched the application DB user from a Postgres superuser to a normal role. The code had been running for months with nearly every tenant-scoped endpoint using the tenant-unaware `get_db` dependency — which yields a session **without** setting `app.tenant_id`. Superusers silently bypass all RLS policies, so queries had appeared to "work" even though the GUC was never set. Ticket RLS-01 swept `Depends(get_db)` → `Depends(get_tenant_db)` across every header-based tenant endpoint, added explicit `set_tenant_context` calls on the remaining path-based A2A endpoints, and added `tests/test_rls_dependency_wired.py` as a regression guard that asserts the RLS GUC is set on each request. The STARTUP-01 `rls_posture` check now `warn`s when it sees a superuser role in use.

RLS policies on all tables enforce:

```sql
CREATE POLICY tenant_isolation ON <table>
  USING (current_setting('app.tenant_id', true)::text = tenant_id)
  WITH CHECK (current_setting('app.tenant_id', true)::text = tenant_id);
```

This means even if application-level filtering has a bug, the database prevents cross-tenant data access. RLS is applied to all tables:

- `workflow_definitions` (adds `is_active` column in migration `0018` — DV-07; does not affect RLS policy)
- `workflow_instances`
- `workflow_snapshots`
- `execution_logs`
- `instance_checkpoints`
- `conversation_sessions`
- `conversation_messages`
- `conversation_episodes` (migration `0013`)
- `memory_profiles`
- `memory_records`
- `entity_facts`
- `a2a_api_keys`
- `tenant_tool_overrides`
- `tenant_secrets`
- `tenant_integrations` (migration `0017`; AE + future external-system connection defaults)
- `tenant_mcp_servers` (migration `0019`; MCP-02 per-tenant MCP server registry)
- `knowledge_bases`
- `kb_documents`
- `kb_chunks`
- `embedding_cache`

Two tables are intentionally **not** tenant-scoped via RLS because they are cross-tenant operator infrastructure:

- `scheduled_triggers` (migration `0015`) — Beat's atomic claim rows for schedule-fire dedupe. Beat is inherently cross-tenant and runs under a `BYPASSRLS` role (see `SETUP_GUIDE.md §5.2a`).
- `async_jobs` and `tenant_mcp_server_tool_fingerprints` — FK-scoped to rows that are themselves tenant-scoped; the parent's RLS policy transitively protects them.

RLS is enabled across the original tables and extended again as new tenant-scoped tables were added (migrations `0014` + `0017` + `0019`).

The memory inspection endpoint (`GET /api/v1/memory/instances/{instance_id}/resolved`) also tenant-filters the resolved `conversation_messages`, `memory_records`, and `entity_facts` rows before returning them, so execution-log metadata cannot be used to bypass tenant isolation.

---

## Credential vault

The vault (`backend/app/security/vault.py`) stores per-tenant secrets encrypted at rest using **Fernet** symmetric encryption.

### Setup

Generate a vault key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it as `ORCHESTRATOR_VAULT_KEY`. Without this key, vault operations will raise a `RuntimeError`.

### How it works

- Each secret is stored in the `tenant_secrets` table with columns: `tenant_id`, `key_name`, `encrypted_value`.
- `encrypt_secret(plaintext)` encrypts using Fernet and returns base64-encoded ciphertext.
- `decrypt_secret(ciphertext)` decrypts back to plaintext.
- Unique constraint on `(tenant_id, key_name)` prevents duplicate keys.

### Management API

Full CRUD is available at `/api/v1/secrets` (see [API Reference](api-reference.md)):

- `POST /api/v1/secrets` — create a secret (key_name + value)
- `GET /api/v1/secrets` — list all secrets (metadata only, values never exposed)
- `PUT /api/v1/secrets/{id}` — update a secret's value
- `DELETE /api/v1/secrets/{id}` — delete a secret

Secret values are encrypted before storage and **never returned** by any API endpoint after creation.

### Management UI

The toolbar has a **Secrets** button (key icon) that opens the `SecretsDialog`. From there users can:

- View all secrets with their key names and last-updated dates
- Copy the `{{ env.KEY_NAME }}` reference to clipboard
- Add new secrets with a password-masked input
- Update existing secret values (old value is never shown)
- Delete secrets (with a warning about broken references)

### Usage in workflows

Node configs can reference vault secrets using `{{ env.KEY_NAME }}` syntax. At execution time, `resolve_config_env_vars` (in `engine/prompt_template.py`) calls `get_tenant_secret(tenant_id, key_name)` to look up and decrypt the matching `TenantSecret`.

This keeps sensitive values (API keys, passwords) out of the `graph_json` while making them available at runtime.

### Vault indirection from registry tables

Both `tenant_integrations` (AutomationEdge connection defaults) and `tenant_mcp_servers` (MCP-02) store `{{ env.KEY }}` placeholders in their `config_json` rather than raw secrets. `engine/integration_resolver.py` (for AE) and `engine/mcp_server_resolver.py` (for MCP) substitute them against `get_tenant_secret` at call time. A missing referenced secret raises loudly — the caller fails rather than sending an unauth'd request a compliant server would 401 anyway.

### Google Cloud auth — Vertex AI (VERTEX-01 + VERTEX-02)

> Full end-to-end Vertex setup + the per-tenant scope caveats live in [Vertex AI Integration](vertex.md). The summary below covers only the security-sensitive bits.

When a node uses `provider: "vertex"`, the runtime authenticates via **Application Default Credentials**, not a vault secret. ADC is resolved in this order:

1. `GOOGLE_APPLICATION_CREDENTIALS` env var pointing at a service-account JSON file.
2. Workload identity on GKE / Cloud Run / Cloud Functions (no JSON file — the runtime mints tokens from the attached service account).
3. `gcloud auth application-default login` (developer laptops only).

**Operational implications:**

* The service account needs the `aiplatform.user` role (or narrower: `aiplatform.endpoints.predict` plus read on the region's model garden).
* No Vertex credential ever lives in `tenant_secrets` or `graph_json`. Rotating the service-account key means replacing the JSON file referenced by the env var — no app restart if ADC re-reads.
* **Per-tenant project routing (VERTEX-02)** — operators register per-tenant GCP projects via the toolbar **Cloud** icon. Rows live in `tenant_integrations` with `system='vertex'` and `config_json={project, location}`. At most one row per tenant is `is_default=true`. Missing row → `ORCHESTRATOR_VERTEX_PROJECT` env fallback. Each tenant's Vertex calls bill to their own project.
* **ADC stays process-global** even after VERTEX-02. `GOOGLE_APPLICATION_CREDENTIALS` and workload identity are per-process, not per-tenant. The orchestrator's service account needs `aiplatform.user` (or narrower) on every GCP project listed in every tenant's registry. Cloud Audit Logs in each tenant's project show the orchestrator's SA, not a tenant-specific SA — so GCP-native auditing cannot distinguish which *tenant* made a given Vertex call. Use orchestrator-side logs / Langfuse traces for that mapping instead. A full per-tenant-identity implementation (SA-JSON swap or workload-identity impersonation) is outlined in [vertex.md §5.3](vertex.md) and is deliberately not scoped yet.
* Vertex embeddings (pre-existing) and Vertex chat (VERTEX-01) both share the same project + location + ADC — one config surface, two code paths.

---

## Rate limiting

Two levels of rate limiting are implemented via `backend/app/security/rate_limiter.py`.

### API request rate

Uses `slowapi` with Redis as the storage backend. Limits are per-tenant (identified by `X-Tenant-Id` header or client IP).

| Setting | Default | Description |
|---------|---------|-------------|
| `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | `100` | Max requests per window |
| `ORCHESTRATOR_RATE_LIMIT_WINDOW` | `1 minute` | Time window |

Exceeding the limit returns **429 Too Many Requests**.

### Execution quota

A separate quota limits workflow executions per tenant per hour:

| Setting | Default | Description |
|---------|---------|-------------|
| `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | `50` | Max executions per hour |

The `check_execution_quota` function counts `WorkflowInstance` rows created in the last hour for the tenant. If the count exceeds the quota, the execute endpoint returns **429** with a message showing current usage.

---

## Security checklist for production

1. **Set a strong `ORCHESTRATOR_SECRET_KEY`** — used for JWT signing. Should be a long random string.

2. **Set `ORCHESTRATOR_VAULT_KEY`** — required for encrypting tenant secrets. Generate with the Fernet command above.

3. **Switch to JWT auth mode** — `ORCHESTRATOR_AUTH_MODE=jwt`. Dev mode trusts client-provided tenant IDs.

4. **Configure CORS** — `ORCHESTRATOR_CORS_ORIGINS` should list only your actual frontend URL(s).

5. **Enable OIDC** if you need SSO — configure the `ORCHESTRATOR_OIDC_*` settings.

6. **Review rate limits** — adjust `ORCHESTRATOR_RATE_LIMIT_REQUESTS` and `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` for your workload.

7. **Run behind TLS** — use a reverse proxy (nginx, Caddy) for HTTPS termination.

8. **Database credentials** — use a non-default password and restrict network access to PostgreSQL.

9. **A2A keys** — rotate regularly. Raw keys are shown only once at creation; hashes are stored.
