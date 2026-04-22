# Vertex AI Integration

The orchestrator talks to **Google Cloud Vertex AI** through the unified `google-genai` SDK, the same library that powers the AI Studio (`provider: "google"`) code path. Only the `Client` constructor differs (`vertexai=True, project, location` vs. `api_key`), so wire format, request shape, response parsing, and tool-calling are shared across both backends.

This page is the canonical reference for how Vertex is wired, what VERTEX-01 and VERTEX-02 together deliver, and — critically — **what they do not deliver**. Operators making compliance or security decisions should read §5 before assuming per-tenant isolation is total.

> **One-line summary.** Vertex is fully supported for chat, ReAct, streaming, and embeddings. Tenants can bill to their own GCP projects (VERTEX-02). *Service-account identity is still shared across the orchestrator process* — audit logs in each tenant's project show the orchestrator's service account, not a tenant-specific one. See §5 for the full caveats table.

---

## 1. What ships today

| Capability | Status | Ticket | File(s) |
|---|---|---|---|
| Vertex embeddings (RAG, Intent Classifier cache, Entity Extractor scoping) | ✅ Pre-existing | — | `engine/embedding_provider.py::_embed_vertex` |
| Gemini chat / agent / ReAct / streaming through Vertex | ✅ VERTEX-01 | `c663450` | `engine/llm_providers.py`, `streaming_llm.py`, `react_loop.py` |
| Per-tenant Vertex project routing | ✅ VERTEX-02 | `cb98bb0` | `engine/llm_providers._resolve_vertex_target`, `api/tenant_integrations.py` |
| **Workflow Authoring Copilot agent (chat loop)** runs on Vertex with `gemini-3.1-pro-preview-customtools` | ✅ COPILOT-01b.iv (Google/Vertex slice) | — | `copilot/agent.py::_call_google` + provider-adapter registry |
| **Copilot `suggest_fix` LLM subcall** reuses the active session's provider — Vertex sessions produce Vertex-hosted fix suggestions | ✅ COPILOT-03.c + Vertex-parity fix | — | `copilot/runner_tools._resolve_suggest_fix_provider` + `_call_suggest_fix_google` |
| **Copilot docs retrieval (SMART-05)** embeds the docs corpus through Vertex when `ORCHESTRATOR_SMART_05_EMBEDDING_PROVIDER=vertex` (pair with `gemini-embedding-001` / `text-embedding-005`) | ✅ SMART-05 | — | `copilot/docs_index._get_or_build_vector_index` via `embedding_provider.get_embeddings_batch_sync` |
| Per-tenant Vertex service-account identity | ❌ Not planned yet | — | Future work; see §5 |
| Per-node Vertex project override | ❌ Not planned yet | — | Would extend `integration_resolver` with `integrationLabel` on LLM nodes |

---

## 2. Operator setup

### 2a. Install the orchestrator's ADC

Vertex uses **Application Default Credentials** — not an API key. Pick one:

| Environment | How |
|---|---|
| Local dev | `gcloud auth application-default login` |
| Self-hosted VM / bare metal | Service-account JSON file + `GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json` |
| GKE / Cloud Run / Cloud Functions | **Workload identity** — bind a KSA/SA with `aiplatform.user` (or narrower) |

The service-account principal needs `roles/aiplatform.user` on *every* GCP project the orchestrator routes traffic to. If you plan multi-project routing via VERTEX-02 (§3), this is one identity with IAM bindings across many projects — see the caveats in §5.

### 2b. Set the default project (fallback)

Operators who want one shared project for all tenants can stop here:

```env
ORCHESTRATOR_VERTEX_PROJECT=my-shared-gcp-project
ORCHESTRATOR_VERTEX_LOCATION=us-central1  # default
```

Every tenant will route to this project unless a registry row overrides it.

### 2c. Register per-tenant projects (optional — VERTEX-02)

Toolbar → **Cloud** icon (`VertexProjectsDialog`). For each tenant that should bill to a different project:

1. **Label** — anything human-readable (`prod-vertex`, `tenant-acme`).
2. **GCP project ID** — e.g., `acme-ml-prod`.
3. **Region** — `us-central1`, `europe-west4`, `asia-east1`, etc. Pick based on data-residency requirements.
4. **Use as default** — exactly one row per tenant may be the default. Flipping the toggle unsets any prior default in the same transaction.

The orchestrator's service account must have `aiplatform.user` on this project. If it doesn't, the first call will fail with a GCP IAM error, not a config error.

### 2d. Reference from a node

Every LLM-calling node (LLM Agent, ReAct Agent, LLM Router, Reflection, Intent Classifier) has a `provider` enum. Pick `"vertex"`:

```json
{
  "provider": "vertex",
  "model": "gemini-2.5-flash"
}
```

The routing happens in the engine — nodes don't pick projects. Swapping a workflow from AI Studio to Vertex is a one-field PATCH.

---

## 3. Resolution precedence

When a node with `provider: "vertex"` runs for tenant T, `engine/llm_providers._resolve_vertex_target(T)` walks this chain:

```
1. tenant_integrations row WHERE tenant_id = T AND system = 'vertex' AND is_default = true
     → config_json.project, config_json.location
     → missing fields fall through to step 3

2. (no row) → go to step 3

3. settings.vertex_project, settings.vertex_location  (env vars)

4. (still empty project) → raise ValueError("ORCHESTRATOR_VERTEX_PROJECT is not configured")
```

**Partial-config fill-in is intentional.** A tenant who only needs to change the region can write a registry row with just `{"location": "europe-west4"}` — `project` will still come from the env default. This keeps the registry minimal when only one field needs overriding.

`tenant_id=None` (internal / cross-tenant scripts) skips the DB lookup entirely and uses env vars.

---

## 4. Where `tenant_id` gets threaded through

VERTEX-02 turned a process-global lookup into a per-call resolver, which means `tenant_id` now flows through the LLM stack as an optional kwarg:

| Entry point | File | Thread through? |
|---|---|---|
| `call_llm(provider, ..., tenant_id=)` | `llm_providers.py` | ✅ dispatched to handler |
| `call_llm_streaming(provider, ..., tenant_id=)` | `llm_providers.py` | ✅ dispatched to stream handler |
| `_PROVIDERS[*]["call"](model, ..., tenant_id=)` | `react_loop.py` | ✅ all four handlers accept the kwarg |
| `_google_client(backend, tenant_id=)` | `llm_providers.py` | ✅ calls `_resolve_vertex_target` |

OpenAI and Anthropic handlers accept `tenant_id` but ignore it — keeps the dispatch uniform and lets future Openai-project-routing fit the same slot without re-plumbing.

Callers that must pass `tenant_id` through:

* `node_handlers._handle_agent` (LLM Agent — streaming)
* `node_handlers._handle_llm_router` (LLM Router)
* `intent_classifier._llm_classify` (NLP Intent Classifier)
* `entity_extractor._llm_extract` (NLP Entity Extractor — LLM fallback)
* `reflection_handler._handle_reflection` (Reflection)
* `memory_service._llm_checkpoint_summary` × 2 (refresh + archive summaries)

A fresh node type that calls an LLM must pass `tenant_id` or its Vertex calls will silently use the env-var project.

---

## 5. Scope caveats — what VERTEX-02 does NOT provide

**Read this section before assuming multi-tenant isolation is complete.** Per-tenant project routing and per-tenant identity are different problems; VERTEX-02 solves the first and leaves the second for a later ticket.

### 5.1 Identity model

| Concern | Shared across tenants? | Notes |
|---|---|---|
| GCP project the API call bills to | ❌ Per-tenant (VERTEX-02) | Controlled by the `tenant_integrations` registry row |
| GCP region the call runs in | ❌ Per-tenant (VERTEX-02) | Data residency works |
| Service-account principal on the outbound call | **✅ Shared (process-global)** | Every call, regardless of tenant, authenticates as the orchestrator's ADC identity |
| Who shows up in the target project's **Cloud Audit Logs** | **✅ Shared** | Audit entries name the orchestrator's SA — not a per-tenant SA |
| Who IAM policies must grant `aiplatform.user` to | **✅ Shared** | The orchestrator's single SA needs access on every tenant's project |

### 5.2 What that means in practice

Concrete implications operators should understand:

* **Tenant A reading Tenant B's billing**: impossible. Vertex calls bill to the target project only, regardless of who initiated them.
* **Tenant A seeing Tenant B's audit trail**: impossible, because each tenant owns their project's audit logs.
* **Can't distinguish which *tenant* made a Vertex call in audit logs**: correct — Cloud Audit Logs see the orchestrator's SA. To tie a specific Vertex call back to a specific tenant, you need *orchestrator-side* logs (SSE stream, execution logs, Langfuse traces). There is no native way in GCP to answer "which tenant hit this model" because tenancy is an orchestrator concept, not a GCP concept under VERTEX-02.
* **A compromised orchestrator SA exposes every tenant's projects**: correct. The SA holds `aiplatform.user` across every listed project; compromising one process compromises the whole fleet's Vertex access. Workload identity + short-lived credentials reduce this risk; long-lived SA-JSON maximises it.
* **Regulated tenant requires their calls to authenticate as their *own* SA**: not solvable with VERTEX-02. That's the per-tenant-identity feature in §5.3.

### 5.3 What a per-tenant-identity feature would look like

Not on the roadmap yet, but documented here so we know the shape when it lands:

**Option A — Tenant-scoped service-account JSON** (simpler, larger secret surface):

1. New field type in `tenant_secrets` for service-account JSON (opaque blob, never logged).
2. Vertex client factory swaps ADC at runtime: read the SA-JSON, call `google.oauth2.service_account.Credentials.from_service_account_info(...)`, pass via `genai.Client(credentials=..., vertexai=True, project, location)`.
3. Each tenant's SA needs `aiplatform.user` on their own project — no cross-tenant grant required.
4. Audit logs correctly show the tenant's SA.

**Option B — Workload-identity impersonation** (cleaner auth, GKE/Cloud-Run only):

1. One "caller" SA on the orchestrator process.
2. Each tenant gets a dedicated "target" SA in their own project.
3. Grant the caller SA `iam.serviceAccountTokenCreator` on each tenant's target SA.
4. At runtime, use `google.auth.impersonated_credentials` to mint a short-lived token for the tenant's target SA; pass those credentials to `genai.Client`.
5. Audit logs show the target SA (the impersonation *is* auditable via the caller SA, if you want the trail).

Both options are 1–2 sprints of work each. Pick when a customer actually asks — premature implementation is more secret-handling surface than the DX is worth.

### 5.4 Data residency is honoured; identity is not

The useful way to summarise: **VERTEX-02 gets you everything except tenant-attributable auth.** That's sufficient for 80%+ of multi-tenant SaaS scenarios (per-tenant billing, per-tenant region, per-tenant quota). The remaining 20% — tenant-attributable audit logs and per-tenant blast-radius — needs per-tenant identity.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ValueError: ORCHESTRATOR_VERTEX_PROJECT is not configured` at dispatch | Neither a registry row nor the env var is set for the tenant making the call | Add a registry row via the Cloud toolbar icon, or set the env var |
| `google.api_core.exceptions.PermissionDenied: 403` | Orchestrator SA lacks `aiplatform.user` on the resolved project | Grant the role in that project's IAM; nothing to change in the orchestrator |
| `google.api_core.exceptions.NotFound: 404 … location` | Location on the registry row is invalid for Vertex in that project | Pick a supported region (`us-central1`, `europe-west4`, `asia-east1`, …); consult Google's Vertex availability table |
| All tenants hitting the same project despite registry rows | `is_default=true` not set on the intended row | Edit the row, tick the default toggle — the API clears any prior default automatically |
| Works for some nodes, fails for others within the same workflow | Some handler didn't pass `tenant_id` to `call_llm` | Open the handler; confirm it threads `tenant_id` — see §4 for the checklist |
| Audit logs don't distinguish tenants | Expected — see §5 | Use orchestrator-side logs / Langfuse traces; GCP can't distinguish them under VERTEX-02 |
| Embeddings work but chat doesn't (or vice versa) | Both use the same ADC + project, so a half-working state usually means the failing path uses a different model — check the model is available in the chosen region | Swap the region or the model |

---

## 7. Related reading

* [Security](security.md) — Vertex ADC section, vault indirection, multi-tenancy RLS model
* [Node Types](node-types.md) — provider selection table on every LLM-using node
* [Feature Roadmap](feature-roadmap.md) — Sprint 2C (VERTEX-01, VERTEX-02)
* [Setup Guide](../SETUP_GUIDE.md) — §1.2 External Services and §7.1 Backend Variables cover ADC and env-var configuration
* [API Reference](api-reference.md) — `tenant_integrations` endpoints handle `system='vertex'` rows alongside AE
* [MCP Audit](mcp-audit.md) — compare how MCP-02 solved the same per-tenant-URL problem for MCP servers; the registry pattern is intentional consistency
