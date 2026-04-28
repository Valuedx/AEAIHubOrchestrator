# Deployment

---

## Prerequisites

| Dependency | Version | Required |
|-----------|---------|----------|
| Python | 3.11+ | Yes |
| Node.js | 18+ | Yes (frontend) |
| PostgreSQL | 16 with pgvector | Yes |
| Redis | 7+ | Optional (required for Celery) |
| Docker & Docker Compose | Latest | Recommended |

---

## Docker Compose

The `docker-compose.yml` at the project root starts the infrastructure services:

```bash
docker compose up -d
```

### Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | `pgvector/pgvector:pg16` | 5432 | Database with vector extension |
| `redis` | `redis:7-alpine` | 6379 | Celery broker (optional) |

PostgreSQL uses the `pgvector` image to ensure the `vector` extension is available for knowledge base embeddings. Data is persisted in a named volume `postgres-data`.

Redis runs with no persistence (`--save "" --appendonly no`) since it is used only as a message broker.

**No application containers** are defined in Docker Compose — the backend and frontend are run directly during development.

---

## Backend setup

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start the server
uvicorn main:app --reload --port 8000
```

### With Celery (optional)

If `ORCHESTRATOR_USE_CELERY=true`, start a Celery worker alongside the backend:

```bash
celery -A app.workers.celery_app worker --loglevel=info
```

Without Celery, tasks run in-process via background threads — no Redis required.

---

## Frontend setup

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

The dev server starts on `http://localhost:8080` by default (or the port configured in `vite.config.ts`).

---

## Environment variables

All backend settings use the `ORCHESTRATOR_` prefix via Pydantic Settings. They can be set in a `.env` file in the `backend/` directory or as system environment variables.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/ae_orchestrator` | PostgreSQL connection string |
| `ORCHESTRATOR_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for Celery |
| `ORCHESTRATOR_SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `ORCHESTRATOR_CORS_ORIGINS` | `["http://localhost:8080", "http://localhost:8082"]` | Allowed CORS origins |
| `ORCHESTRATOR_AUTH_MODE` | `dev` | `dev` (`X-Tenant-Id` header), `jwt` (pre-issued Bearer), or `local` (username/password — LOCAL-AUTH-01). OIDC is additive via `ORCHESTRATOR_OIDC_ENABLED` regardless of mode. |
| `ORCHESTRATOR_USE_CELERY` | `false` | Enable Celery worker; false = in-process threads |

### LLM providers

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_GOOGLE_API_KEY` | `""` | Google GenAI API key |
| `ORCHESTRATOR_GOOGLE_PROJECT` | `""` | Google Cloud project |
| `ORCHESTRATOR_GOOGLE_LOCATION` | `us-central1` | Google Cloud region |
| `ORCHESTRATOR_OPENAI_API_KEY` | `""` | OpenAI API key |
| `ORCHESTRATOR_OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI base URL (for proxies) |
| `ORCHESTRATOR_ANTHROPIC_API_KEY` | `""` | Anthropic API key |

### MCP

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_MCP_SERVER_URL` | `http://localhost:8000/mcp` | **Fallback** MCP server URL. Used only when a tenant has no `tenant_mcp_servers` row (the MCP-02 per-tenant registry takes precedence — operators register servers via the toolbar Globe icon). See `codewiki/mcp-audit.md`. |
| `ORCHESTRATOR_MCP_POOL_SIZE` | `4` | Warm sessions per `(tenant, server)` pool. Multiple tenants / multiple servers each get their own pool keyed by `(tenant_id, pool_key)`. |

### Knowledge Base / RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_EMBEDDING_DEFAULT_PROVIDER` | `openai` | Default embedding provider |
| `ORCHESTRATOR_EMBEDDING_DEFAULT_MODEL` | `text-embedding-3-small` | Default embedding model |
| `ORCHESTRATOR_EMBEDDING_BATCH_SIZE` | `100` | Max texts per embedding API call |
| `ORCHESTRATOR_KB_MAX_FILE_SIZE_MB` | `50` | Upload size limit |
| `ORCHESTRATOR_KB_DEFAULT_VECTOR_STORE` | `pgvector` | Default vector store |
| `ORCHESTRATOR_KB_DEFAULT_CHUNKING_STRATEGY` | `recursive` | Default chunking strategy |
| `ORCHESTRATOR_FAISS_INDEX_DIR` | `./faiss_indexes` | FAISS index file directory |
| `ORCHESTRATOR_VERTEX_PROJECT` | `""` | GCP project for Vertex AI. Used by **both** Gemini chat/agent nodes (when `provider: "vertex"`) and Vertex-backed embedding providers. Auth is ADC — point `GOOGLE_APPLICATION_CREDENTIALS` at a service-account JSON, or use workload identity on GKE / Cloud Run. |
| `ORCHESTRATOR_VERTEX_LOCATION` | `us-central1` | Vertex AI region — applies to both chat and embeddings |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_VAULT_KEY` | `""` | Fernet encryption key for tenant secrets |
| `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | `100` | Max requests per window |
| `ORCHESTRATOR_RATE_LIMIT_WINDOW` | `1 minute` | Rate limit window |
| `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | `50` | Max workflow executions per hour per tenant |

### OIDC (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_OIDC_ENABLED` | `false` | Enable OIDC authentication |
| `ORCHESTRATOR_OIDC_ISSUER` | `""` | OIDC issuer URL |
| `ORCHESTRATOR_OIDC_CLIENT_ID` | `""` | OAuth client ID |
| `ORCHESTRATOR_OIDC_CLIENT_SECRET` | `""` | OAuth client secret |
| `ORCHESTRATOR_OIDC_REDIRECT_URI` | `http://localhost:8001/auth/oidc/callback` | Callback URL |
| `ORCHESTRATOR_OIDC_TENANT_CLAIM` | `email` | ID token claim used as tenant_id |
| `ORCHESTRATOR_OIDC_SCOPES` | `openid email profile` | OAuth scopes |

### Local password auth (LOCAL-AUTH-01, optional)

Set `ORCHESTRATOR_AUTH_MODE=local` to activate. Active Directory / LDAP binding is explicitly deferred; see [Security](security.md) §Local password mode.

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_PASSWORD_MIN_LENGTH` | `8` | Minimum password length enforced at create / reset time |
| `ORCHESTRATOR_LOCAL_ADMIN_USERNAME` | `""` | Bootstrap admin username — seeded once on first boot into `auth_mode=local` |
| `ORCHESTRATOR_LOCAL_ADMIN_PASSWORD` | `""` | Bootstrap admin password — consumed only when the seed row does not yet exist |
| `ORCHESTRATOR_LOCAL_ADMIN_TENANT_ID` | `default` | Tenant the bootstrap admin is created under |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_LANGFUSE_ENABLED` | `false` | Enable Langfuse tracing |
| `ORCHESTRATOR_LANGFUSE_PUBLIC_KEY` | `""` | Langfuse public key |
| `ORCHESTRATOR_LANGFUSE_SECRET_KEY` | `""` | Langfuse secret key |
| `ORCHESTRATOR_LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse endpoint |

### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_MAX_SNAPSHOTS` | `20` | Max version snapshots per workflow (0 = unlimited) |

### Frontend environment variables

Set in a `.env` file in the `frontend/` directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:8000` | Backend API base URL |
| `VITE_TENANT_ID` | `default` | Tenant ID for dev mode |
| `VITE_AUTH_MODE` | `dev` | `dev` (headers only), `oidc` (SSO login gate), or `local` (username/password gate — LOCAL-AUTH-01) |
| `VITE_OIDC_AUTHORITY` | — | OIDC provider URL |
| `VITE_OIDC_CLIENT_ID` | — | OIDC client ID |

---

## Database migrations

Migrations are in `backend/alembic/versions/` and use a linear revision chain.

```bash
cd backend

# Apply all migrations
alembic upgrade head

# Check current revision
alembic current

# Generate a new migration
alembic revision --autogenerate -m "description"
```

Migration `0009` creates the `vector` extension and knowledge base tables. It requires the `pgvector` extension to be available in the PostgreSQL instance (provided by the `pgvector/pgvector:pg16` Docker image).

---

## Dependencies

### Backend (`requirements.txt`)

Key packages:

| Package | Purpose |
|---------|---------|
| `fastapi`, `uvicorn` | Web framework and ASGI server |
| `sqlalchemy`, `alembic` | ORM and migrations |
| `psycopg2-binary` | PostgreSQL driver |
| `pydantic-settings` | Configuration management |
| `celery`, `redis` | Task queue (optional) |
| `openai` | OpenAI LLM and embedding provider |
| `google-genai` | Google GenAI provider |
| `google-cloud-aiplatform` | Vertex AI embeddings |
| `anthropic` | Anthropic LLM provider |
| `mcp` | MCP SDK client |
| `pgvector` | pgvector SQLAlchemy integration |
| `faiss-cpu` | FAISS vector search |
| `pymupdf` | PDF parsing |
| `tiktoken` | Token-based chunking |
| `numpy` | Vector operations |
| `cryptography` | Fernet vault encryption |
| `langfuse` | Observability tracing |
| `jinja2` | Prompt templating |

### Frontend (`package.json`)

Key packages: `react`, `@xyflow/react`, `zustand`, `tailwindcss`, `lucide-react`, `@radix-ui/*` (via shadcn).

---

## Production considerations

1. **Set `ORCHESTRATOR_SECRET_KEY`** to a strong random value for JWT signing.
2. **Set `ORCHESTRATOR_VAULT_KEY`** to a Fernet key for encrypting tenant secrets.
3. **Switch `ORCHESTRATOR_AUTH_MODE`** to `jwt` (external IdP), `local` (built-in username/password — seed a bootstrap admin via `ORCHESTRATOR_LOCAL_ADMIN_USERNAME` / `_PASSWORD` on first boot), or enable OIDC SSO for real authentication.
4. **Enable Celery** (`ORCHESTRATOR_USE_CELERY=true`) for production workloads to avoid blocking the API server.
5. **Use `pgvector`** (not FAISS) for knowledge base storage in production — FAISS indexes are ephemeral in containerized environments.
6. **Configure CORS origins** to match your actual frontend URL.
7. **Run behind a reverse proxy** (nginx, Caddy, etc.) with TLS termination.
8. **Monitor with Langfuse** by enabling the observability settings.
