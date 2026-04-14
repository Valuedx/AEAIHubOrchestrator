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

The frontend (`VITE_AUTH_MODE=oidc`) shows a `LoginPage` and stores the access token in `localStorage` as `ae_access_token`.

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

PostgreSQL Row Level Security (RLS) policies provide defense-in-depth. The backend sets a session-level variable before each query:

```sql
SET LOCAL app.tenant_id = '<tenant_id>';
```

RLS policies on all tables enforce:

```sql
CREATE POLICY tenant_isolation ON <table>
  USING (current_setting('app.tenant_id', true)::text = tenant_id)
  WITH CHECK (current_setting('app.tenant_id', true)::text = tenant_id);
```

This means even if application-level filtering has a bug, the database prevents cross-tenant data access. RLS is applied to all tables:

- `workflow_definitions`
- `workflow_instances`
- `workflow_snapshots`
- `execution_logs`
- `instance_checkpoints`
- `conversation_sessions`
- `a2a_api_keys`
- `tenant_tool_overrides`
- `tenant_secrets`
- `knowledge_bases`
- `kb_documents`
- `kb_chunks`

RLS is enabled in migrations `0001` (original tables) and `0009` (knowledge base tables).

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

### Usage in workflows

Node configs can reference vault secrets using `{{ env.KEY_NAME }}` syntax. At execution time, `resolve_config_env_vars` (in `engine/prompt_template.py`) decrypts the matching `TenantSecret` and injects the plaintext value.

This keeps sensitive values (API keys, passwords) out of the `graph_json` while making them available at runtime.

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
