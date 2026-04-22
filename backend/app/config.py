from pydantic_settings import BaseSettings

from app.paths import BACKEND_ENV_FILE


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ae_orchestrator"
    redis_url: str = "redis://localhost:6379/0"
    mcp_server_url: str = "http://localhost:8000/mcp"
    secret_key: str = "change-me-in-production"
    cors_origins: list[str] = ["http://localhost:8080", "http://localhost:8082"]

    # Auth: "dev" = X-Tenant-Id header, "jwt" = Bearer token required
    auth_mode: str = "dev"

    # LLM provider keys
    google_api_key: str = ""
    google_project: str = ""
    google_location: str = "us-central1"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    anthropic_api_key: str = ""

    # Credential vault encryption key (Fernet, base64-encoded 32 bytes).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    vault_key: str = ""

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window: str = "1 minute"  # DEPRECATED — see rate_limit_window_seconds
    # ADMIN-02 — integer-seconds window used by the real rate-limit
    # middleware. Supersedes the old slowapi-format string above, which
    # was never actually enforced (no middleware was installed). Kept as
    # an env default when a tenant has no tenant_policies override.
    rate_limit_window_seconds: int = 60
    execution_quota_per_hour: int = 50

    # STARTUP-01 — silence the preflight-check pass during tests that
    # spin up a FastAPI app but don't need real DB/Redis/Celery IO.
    skip_startup_checks: bool = False

    # OIDC federation (optional — set oidc_enabled=true to activate)
    oidc_enabled: bool = False
    oidc_issuer: str = ""                          # e.g. https://accounts.google.com
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8001/auth/oidc/callback"
    oidc_tenant_claim: str = "email"               # ID token claim used as tenant_id
    oidc_scopes: str = "openid email profile"

    # Snapshot pruning — max snapshots per workflow (0 = unlimited)
    max_snapshots: int = 20

    # MCP connection pool size
    mcp_pool_size: int = 4

    # SMART-04 — global default for the copilot's proactive authoring
    # lints. Per-tenant override lives on
    # ``tenant_policies.smart_04_lints_enabled``; this env default is
    # the fallback for tenants with no row. Default true — lints are
    # zero-LLM-cost and strictly additive to UX.
    smart_04_lints_enabled: bool = True

    # SMART-06 — global default for the copilot's proactive MCP tool
    # discovery (agent calls list_tools on tenant-registered MCP
    # servers). Per-tenant override on
    # ``tenant_policies.smart_06_mcp_discovery_enabled``. Default
    # true — discovery is a cached list_tools call, zero-LLM-cost.
    smart_06_mcp_discovery_enabled: bool = True

    # SMART-02 — global default for the accepted-patterns library.
    # Default true; zero LLM cost (pure DB I/O). Off means the
    # agent gets no few-shot retrieval from prior promotes, and
    # promote doesn't persist a pattern row.
    smart_02_pattern_library_enabled: bool = True

    # SMART-01 — two flags, BOTH default off. Unlike SMART-02/04/06
    # these spend real engine tokens, so the cost-conscious default
    # is opt-in. Per-tenant overrides on
    # ``tenant_policies.smart_01_*``.
    #
    #   scenario_memory:  every successful execute_draft auto-saves
    #                     a scenario (deduped by payload hash).
    #   strict_promote_gate: promote refuses with 400 on any failing
    #                     scenario — no "promote anyway" override.
    smart_01_scenario_memory_enabled: bool = False
    smart_01_strict_promote_gate_enabled: bool = False

    # SMART-05 — vector-backed docs search for the copilot. Off by
    # default because embedding calls cost tokens. When on, the
    # first ``search_docs`` call per process embeds every codewiki
    # chunk (~30–50 entries) and caches in-process; subsequent
    # queries embed the query and rank by cosine similarity. A
    # per-call fallback to the 01b.iii word-overlap path covers
    # the "embedding provider unavailable" case so turning this
    # on never returns *fewer* results than off.
    smart_05_vector_docs_enabled: bool = False
    # Which embedding provider / model to use for SMART-05. The
    # model must exist in ``embedding_provider.EMBEDDING_REGISTRY``.
    # Default matches the knowledge-base defaults so operators who
    # already have OpenAI creds don't need extra config.
    smart_05_embedding_provider: str = "openai"
    smart_05_embedding_model: str = "text-embedding-3-small"

    # Optional: process-wide default URL for the AutomationEdge Copilot
    # (a separate product the workflow authoring copilot can hand off to
    # when the user wants to design deterministic RPA steps before wiring
    # them into this orchestrator via an AutomationEdge node). A
    # per-tenant override can live on
    # ``tenant_integrations(system='automationedge').config_json.copilotUrl``;
    # this env default is the fallback. Empty = handoff surfaces as
    # "open AE Copilot in your AE environment" without a deep link.
    ae_copilot_url: str = ""

    # When False, tasks run in-process via background threads (no Redis/Celery needed)
    use_celery: bool = False

    # Knowledge Base / RAG
    embedding_default_provider: str = "openai"
    embedding_default_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 100
    kb_max_file_size_mb: int = 50
    kb_default_vector_store: str = "pgvector"
    kb_default_chunking_strategy: str = "recursive"
    faiss_index_dir: str = "./faiss_indexes"

    # Google Vertex AI (for embeddings — separate from google_api_key / GenAI)
    vertex_project: str = ""
    vertex_location: str = "us-central1"

    # Code Execution Sandbox
    code_sandbox_enabled: bool = True
    code_sandbox_timeout_max: int = 120
    code_sandbox_output_limit_bytes: int = 1_048_576  # 1 MB

    # Langfuse Observability
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    model_config = {
        "env_prefix": "ORCHESTRATOR_",
        "env_file": str(BACKEND_ENV_FILE),
        "extra": "ignore"
    }


settings = Settings()
