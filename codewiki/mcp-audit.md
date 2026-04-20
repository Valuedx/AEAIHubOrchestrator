# MCP Client Audit — current state vs. the 2025-06-18 spec

**MCP-01 — Sprint 2B**

> **Status update (MCP-02 shipped):** the single-server env-var path
> described in §1 is now a *fallback*, not the primary path. Operators
> register per-tenant MCP servers via the Toolbar → Globe icon; see
> the new "Per-tenant MCP server registry" section at the bottom of
> this doc for the registry shape and resolver semantics. The backlog
> in §6 is otherwise unchanged.



This document audits `backend/app/engine/mcp_client.py` and the surfaces that depend on it against the [Model Context Protocol specification, revision 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18) and the 2025-11-25 follow-up revision. It names every meaningful gap and converts each into a ranked, actionable follow-up ticket. The intent is *not* to catch up on everything at once — half of what we're missing isn't load-bearing for our use cases — but to make the status visible so every future sprint can pick a gap off the top of the list deliberately.

> **Reading order.** §1 summarises what we have today. §§2–5 walk the spec area-by-area with concrete "what we do" / "what the spec says" / "gap" deltas. §6 is the ranked backlog. §7 is the MCP-02 scoping note.

---

## 1. Current implementation at a glance

| Concern | Today |
|---|---|
| **Transport** | Streamable HTTP via Python `mcp[cli]>=1.20.0` (`ClientSession` + `streamablehttp_client`) |
| **Server set** | **One** server per process, URL from `settings.mcp_server_url` env var |
| **Tenant scoping** | None at the MCP layer; every tenant on one process shares one MCP endpoint |
| **Auth** | Whatever the configured URL accepts (usually a shared bearer baked into the URL or headers); no OAuth flow, no 401/`WWW-Authenticate` handling |
| **Primitives used** | `tools/list`, `tools/call` only |
| **Primitives ignored** | resources, prompts, elicitation, completion, sampling, logging |
| **Result parsing** | Reads `content[].text`, JSON-parses if possible, else wraps as `{"result": raw_text}`. Ignores `structuredContent` + `outputSchema`. |
| **Caching** | 300-second process-global `list_tools` cache (`_TOOL_CACHE_TTL`). No invalidation on `notifications/tools/list_changed`. |
| **Session management** | 4-connection pool (`_MCPSessionPool`), warm sessions reused. Falls back to per-call session on pool error. No resumability logic (`Last-Event-ID`). SDK handles `Mcp-Session-Id` / `MCP-Protocol-Version` for us. |
| **Tenant overrides** | `TenantToolOverride` rows can *disable* a tool per-tenant from the shared catalogue. That's the only per-tenant dial. |
| **Security posture** | No HITL confirmation before `tools/call`; no SSRF guard on the configured URL; no pinning / diffing of tool definitions across fetches; tool annotations (`destructiveHint` etc.) are not surfaced to the UI or enforced anywhere. |

Entry points: `backend/app/engine/mcp_client.py`, `backend/app/api/tools.py`, `backend/app/engine/node_handlers.py::_handle_action` (MCP Tool node), `backend/app/engine/react_loop.py` (LLM tool-calling loop). Config: `backend/app/config.py` (`mcp_server_url`, `mcp_pool_size`).

---

## 2. Transport

### What the spec requires

* **Streamable HTTP** replaces the deprecated HTTP+SSE transport. stdio is also spec-standard but not relevant for a hosted orchestrator.
* `MCP-Protocol-Version: 2025-06-18` header on every non-initialize HTTP request; missing → server assumes `2025-03-26`; invalid → HTTP 400.
* `Mcp-Session-Id` is assigned by the server on `InitializeResult`, echoed by the client on every subsequent request. On HTTP 404 with a session ID, the client MUST start a new session. On app exit, the client SHOULD send `HTTP DELETE` with the `Mcp-Session-Id` header to release server state.
* On broken connection, client SHOULD reconnect via GET with `Last-Event-ID` for resumable replay on the same stream.
* Server validates the `Origin` header (DNS-rebinding defence) — server-side MUST, but a co-located local server must enforce it.
* Disconnection is NOT a cancellation; cancellation requires an explicit `CancelledNotification`.

The **2025-11-25 revision** clarifies that invalid `Origin` → HTTP 403 (not 400), adds polling semantics for SSE streams (SEP-1699), and permits stdio servers to use stderr for all logging.

### What we do

We delegate all of this to the `mcp` SDK's `streamablehttp_client` via `_MCPSessionPool._create_session`. The SDK (version ≥ 1.20.0) handles `Mcp-Session-Id` and `MCP-Protocol-Version` internally and emits `initialize` on session entry. We do not implement resumability ourselves; we do not send an explicit `HTTP DELETE` on shutdown — `shutdown_pool()` closes the transport async context which terminates the session cleanly at the TCP/HTTP layer but does not issue the spec's soft-close verb.

### Gaps

* **G-T1 (low impact)**: no explicit `HTTP DELETE` session release on shutdown. Cost: server keeps stale session state until timeout. Benefit of fixing: minor — mostly a politeness to strict servers that count sessions.
* **G-T2 (unverified)**: resumability via `Last-Event-ID` on connection break is SDK-implemented but we have no end-to-end test. Cost: on a flaky network a long-running tool call may be retried from scratch. Benefit of fixing: medium for Diverted-scale workflows; low for normal tool calls which complete in seconds. Action: add an integration test that kills and resumes a stream.
* **G-T3 (low impact)**: targeting the 2025-06-18 protocol version — 2025-11-25 adds `Origin` → 403, SSE polling, and assorted server-side clarifications. None of them change client behavior we rely on, but bumping `MCP-Protocol-Version` when the SDK supports it is cheap.

---

## 3. Authorization (OAuth 2.1 resource-server model)

### What the spec requires

Mandatory for HTTP clients (SHOULD conform; stdio is exempted). The requirements are worth quoting because they are numerous and strict:

* Clients **MUST** use OAuth 2.0 Protected Resource Metadata (RFC 9728) for authorization-server discovery.
* Clients **MUST** parse `WWW-Authenticate` headers on HTTP 401 and respond appropriately.
* Clients **MUST** implement Resource Indicators (RFC 8707) — the `resource` parameter on both authorization and token requests, identifying the MCP server by its canonical URI, sent *regardless* of whether the authorization server advertises support.
* Clients **MUST** implement PKCE (OAuth 2.1 §7.5.2).
* Clients **MUST** use the `Authorization: Bearer` header; **MUST NOT** put tokens in the URI query string.
* Clients **MUST NOT** send tokens issued by an authorization server other than the MCP server's own.
* Servers **MUST NOT** pass tokens upstream to third-party APIs (token passthrough is explicitly forbidden).
* Clients **SHOULD** support Dynamic Client Registration (RFC 7591).

Discovery flow: 401 → parse `WWW-Authenticate` → `GET /.well-known/oauth-protected-resource` → parse `authorization_servers` → `GET /.well-known/oauth-authorization-server` → (optional DCR) → PKCE authorize with `resource=<mcp-url>` → token → retry with bearer.

### What we do

Nothing. We rely on whatever static auth the configured `mcp_server_url` accepts (typically a pre-baked bearer in an HTTP header the SDK has been told to include, or no auth at all if the server is on a private network). There is no 401 handler, no discovery, no PKCE, no resource-indicator parameter. The Python `mcp` SDK's `streamablehttp_client` does not auto-run an OAuth flow — it accepts an `auth` callable the caller provides, but we don't pass one.

### Gaps

* **G-A1 (critical for external servers)**: if `mcp_server_url` ever points at a spec-compliant protected resource, we get HTTP 401 and fail. Cost: blocks connecting to any hosted MCP server that enforces the spec's auth model (which is the direction the ecosystem is moving). Benefit of fixing: high — it unblocks using public MCP services as tool providers.
* **G-A2 (critical if we build MCP-02)**: the registry design must support OAuth discovery mode, not just static-bearer mode. Building the registry with only a "header map" column would paint us into a corner. See §7.
* **G-A3 (hardening, smaller impact)**: SSRF guard on the configured MCP URL — the spec flags this explicitly because discovery follows server-controlled `.well-known` redirects. We never do discovery so this is currently moot, but the moment we add OAuth it becomes a MUST-do.

---

## 4. Primitives beyond tools

| Primitive | What it enables | Spec status | Ours |
|---|---|---|---|
| **Tools** (`tools/list`, `tools/call`) | LLM-callable functions | MUST-support for tool-using clients | ✅ |
| **Resources** (`resources/list`, `resources/read`, `resources/subscribe`, `notifications/resources/*`) | Server-exposed context (files, DB rows, URIs) that the *host* selects and injects into LLM context. Application-driven. | OPTIONAL | ❌ |
| **Prompts** (`prompts/list`, `prompts/get`) | Server-provided prompt templates invoked explicitly by the user (slash commands). User-controlled. | OPTIONAL | ❌ |
| **Elicitation** (`elicitation/create`) | Server asks the user for structured input mid-tool-call via a flat JSON Schema. Client answers `accept` / `decline` / `cancel`. | OPTIONAL (added 2025-06-18, marked evolving) | ❌ |
| **Sampling** (`sampling/createMessage`) | Server asks the host to run an LLM inference on its behalf. | OPTIONAL | ❌ |
| **Completion** (`completion/complete`) | Argument-autocompletion hints from the server. | OPTIONAL | ❌ |
| **Logging** (`logging/setLevel`, `notifications/message`) | Server-sourced log stream for debugging. | OPTIONAL | ❌ |
| **Structured tool output** (`structuredContent`, `outputSchema`) | Tools return a typed JSON object validated against a schema; legacy `content[]` stringified JSON remains as fallback. | OPTIONAL (added 2025-06-18) | ❌ |

### Gaps

* **G-P1 (medium impact — lossy)**: structured tool output is ignored. Modern servers increasingly return `structuredContent` + an `outputSchema`; we get the stringified fallback in `content[0].text`. Cost: we lose schema validation, and some servers may shrink the text block assuming clients read the structured form. Benefit of fixing: medium — cheap to add (`if result.structuredContent: …`), nets real schema validation for condition nodes that consume tool output.
* **G-P2 (medium impact — capability)**: no elicitation support. Any server that wants to prompt the user mid-call is incompatible. Cost: blocks interactive tools (a "deploy to prod — confirm?" tool cannot be wired to an autonomous workflow, but it also can't be wired from a human-in-the-loop workflow). Benefit of fixing: medium. Fits naturally on top of our existing HITL resume path: an `elicitation/create` suspends the DAG with `suspended_reason='mcp_elicitation'`, operators answer via a new modal, we resume.
* **G-P3 (low impact for now)**: no prompts / resources support. These are primarily host-UI concerns (user picks a resource or slash-command). Our orchestrator is headless execution, so they're mostly irrelevant — though a "resource picker" field on nodes could let operators pin resources into context at design time. Defer.
* **G-P4 (low impact)**: no sampling support. Would let a tool call out to our LLM. Neat, but rarely used in practice, and the security review would dwarf the implementation.

---

## 5. Security posture

The [Security Best Practices page](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices) and the [Tools page](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) together set several client-side MUST/SHOULD expectations. We meet one, miss the rest.

| Concern | Spec says | Us |
|---|---|---|
| Human-in-the-loop for tool calls | "Applications **SHOULD** … Present confirmation prompts to the user for operations, to ensure a human is in the loop" | ❌ No confirmation UI. Tool calls fire on execute. |
| Show tool inputs before calling | "Show tool inputs to the user before calling the server, to avoid malicious or accidental data exfiltration" | ❌ We log them, but don't surface pre-call in the UI. |
| Tool annotations are untrusted | "Clients **MUST** consider tool annotations to be untrusted unless they come from trusted servers" | N/A — we don't read annotations at all. |
| Token passthrough | Forbidden (confused-deputy root cause) | N/A — we don't forward tokens. |
| SSRF during OAuth discovery | Client "**MUST** consider SSRF risks"; HTTPS required (except loopback); block private / link-local / metadata ranges | N/A today; will apply the moment we add OAuth. |
| Session hijacking | Session IDs non-deterministic; sessions MUST NOT be used for authentication | Delegated to SDK; we don't use session ID as auth. ✅ |
| Tool-poisoning / rug-pull | Ecosystem recommendation (not yet a spec MUST): pin/diff tool definitions across fetches, re-prompt on definition change | ❌ 5-min cache masks this; we happily call a tool whose description/schema drifted since the user approved it. |

### Gaps

* **G-S1 (high impact for destructive tools)**: no HITL confirmation gate. Any workflow author can wire an arbitrary MCP tool and it fires unattended. Cost: a `delete_customer` tool is one config field away from a production incident. Benefit of fixing: high. Natural hook: extend the Condition / validation layer to surface tool annotations (`destructiveHint: true`) as an explicit "confirm before run" flag at design time, and enforce at runtime via a HITL suspend if the workflow is marked non-autonomous. Pairs well with DV-03 / DV-07 semantics.
* **G-S2 (medium impact — defense in depth)**: no tool-definition pinning. Every `list_tools` call takes whatever the server returns. Cost: a hostile or hijacked server can swap `send_invoice(customer_id)` for `send_invoice(customer_id, ..., amount)` and the workflow silently calls the new shape. Benefit of fixing: medium. Add a content-hash of `(name, description, inputSchema)` per tool per server and prompt the user when it changes.
* **G-S3 (low impact today, high once we wire OAuth)**: no SSRF guard on the MCP URL. Required defense for a spec-compliant OAuth client; not urgent for our static-URL setup.

---

## 6. Ranked backlog (→ MCP-03+)

Ranked by impact-in-our-context (not spec severity). Each is a candidate sprint ticket.

| # | Title | Rough size | Rationale |
|---|---|---|---|
| **MCP-02** | **Per-tenant MCP server registry** *(already scoped — see §7)* | 1 sprint | Unblocks real multi-tenant deployment. Everything below assumes this exists. |
| **MCP-03** | OAuth 2.1 resource-server client (discovery + PKCE + RFC 8707 `resource`) | 1 sprint | Required to connect to hosted MCP services; forced by ecosystem direction. **Registry (MCP-02) must ship with room in its schema for this.** |
| **MCP-04** | HITL confirmation gate for destructive tools | Half-sprint | Biggest production-risk gap today. Surface `destructiveHint` / `readOnlyHint` at design time; gate at runtime via HITL suspend. Piggybacks on existing suspend/resume machinery. |
| **MCP-05** | Structured tool output (`structuredContent` + `outputSchema` validation) | Small (few days) | Cheap; fixes lossy result parsing and lets Condition nodes validate tool output against a schema. |
| **MCP-06** | Tool-definition pinning + drift detection | Half-sprint | Mitigates tool-poisoning. Store `(server_id, tool_name) → sha256(name + description + inputSchema)`; surface drift on next `list_tools`. |
| **MCP-07** | Elicitation support (`elicitation/create` → HITL suspend) | Half-sprint | Unlocks interactive MCP tools. Natural extension of existing HITL path with a new `suspended_reason='mcp_elicitation'`. |
| **MCP-08** | `notifications/tools/list_changed` subscription + cache invalidation | Small | Today: 5-min staleness window. Tomorrow: immediate invalidation on notification. |
| **MCP-09** | `HTTP DELETE` session release on shutdown + resumability integration test | Small | Politeness + confidence in the SDK's reconnect path. |
| MCP-10 | Protocol version bump to 2025-11-25 | Small | Cheap; pick up `Origin` 403, SEP-1699 SSE polling. Non-breaking. |
| MCP-11 | Sampling (`sampling/createMessage`) support | Unscoped | Rarely used in practice; security review dominates implementation cost. **Defer.** |
| MCP-12 | Resources / prompts primitives | Unscoped | Host-UI concerns; low value for a headless orchestrator. **Defer.** |

---

## 7. MCP-02 scoping note (what the audit changed)

MCP-02 was originally scoped as "a `tenant_mcp_servers` table + CRUD + picker." The audit adds three constraints on its schema:

1. **Auth is not a header map.** A single `headers_json` column covers static-bearer and API-key-in-header servers, but forecloses OAuth (MCP-03) where the client needs to remember a client ID, a discovered authorization-server URL, PKCE state, refresh token handling, and the canonical resource URI. The schema should have an `auth_mode` discriminator (`none` | `static_headers` | `oauth_2_1`) and separate columns / JSON blobs for each mode's config.
2. **Per-server tool-def pinning (MCP-06) needs a table.** If we only ship MCP-02 now, add a `tenant_mcp_server_tool_fingerprints` side table now (nullable, empty at first) so MCP-06 doesn't require another migration.
3. **The `settings.mcp_server_url` env path must keep working.** Add a synthetic "legacy fallback" registry row on first migration for any tenant that already has workflows referencing the default server — or, simpler, keep the env-var path as an implicit "unconfigured tenant" default and write the registry row only when the operator edits it.

None of these block starting MCP-02; they constrain its schema. The audit closes here; MCP-02 begins when you're ready.

---

## 8. Per-tenant MCP server registry — MCP-02 as shipped

### Operator flow

1. Open the Toolbar → **Globe** icon.
2. Click **Add server**. Give the server a label (used in node configs), its Streamable HTTP URL, and an auth mode.
3. For `static_headers`, drop a block of `Name: value` headers. Values may embed `{{ env.KEY_NAME }}` placeholders — at call time the resolver substitutes them from the Fernet-encrypted Secrets vault, so raw tokens never live in the registry row.
4. Tick **Use as default** if this server should serve every MCP Tool / ReAct Agent whose node config leaves `mcpServerLabel` blank. Only one default per tenant is allowed — the partial unique index `ux_tenant_mcp_server_default` enforces it at the DB layer.
5. Reference the server from a node via the MCP Tool node's new `mcpServerLabel` config field (blank = default).

### Resolution precedence

`backend/app/engine/mcp_server_resolver.py::resolve_mcp_server` is the single source of truth. Precedence, highest first:

1. Explicit `server_label` matches a row in `tenant_mcp_servers` for this tenant.
2. The tenant's `is_default=True` row.
3. `settings.mcp_server_url` — the pre-MCP-02 env-var fallback so tenants with zero registry rows keep working untouched.

Auth-mode dispatch also lives in the resolver so `mcp_client` stays dumb:

* `none` — no auth headers added.
* `static_headers` — `config_json.headers` is read, `{{ env.KEY }}` placeholders resolved via `vault.get_tenant_secret(tenant, KEY)`. Missing secrets raise `McpServerResolutionError` loudly (better than sending an unauth'd request that a compliant server would 401 anyway).
* `oauth_2_1` — accepted by the API so registry rows can be created ahead of MCP-03, but the runtime raises `McpServerResolutionError(not yet implemented)`. The auth-mode dropdown in the UI disables this option.

### Schema (migration 0019)

```
tenant_mcp_servers
  id            UUID PK
  tenant_id     VARCHAR(64)        — RLS scope
  label         VARCHAR(128)       — unique per (tenant_id, label)
  url           VARCHAR(1024)
  auth_mode     VARCHAR(32)        — CHECK IN ('none','static_headers','oauth_2_1')
  config_json   JSONB              — { headers: {...} } for static_headers
  is_default    BOOLEAN            — partial unique index: only one true per tenant
  created_at, updated_at

tenant_mcp_server_tool_fingerprints       -- forward-declared for MCP-06
  id, server_id → tenant_mcp_servers(id), tool_name, fingerprint_sha256,
  last_seen_at
```

Both tables enable RLS with the standard `tenant_id = current_setting('app.tenant_id', true)` policy (mirrors 0001 / 0014 / 0017).

### Session pool changes

`mcp_client._MCPSessionPool` is now keyed by `(tenant_id or "__env__", pool_key)` where `pool_key` is the server row id (or `"__env_fallback__"` for the env path). This isolates tenants from each other at the connection layer — a misbehaving server for tenant A can't pin a session that tenant B would pick up. The 300-second `list_tools` cache is keyed the same way.

### Caller surface

All three call sites now thread `tenant_id` + `server_label`:

* `backend/app/api/tools.py::list_tools` — optional `?server_label=` query param.
* `backend/app/engine/node_handlers.py::_call_mcp_tool` — reads `config.mcpServerLabel`.
* `backend/app/engine/react_loop.py::_execute_tool` / `_load_tool_definitions` — reads `config.mcpServerLabel`.

Leaving `server_label` unset keeps the old path — default row, or env-var fallback.

### Related tests

* `backend/tests/test_tenant_mcp_servers_api.py` — 14 tests: create / list / get / update / delete, 409 on label collision, default-swapping clears the prior default in the same transaction, 422 on invalid auth_mode, oauth_2_1 accepted at the API layer.
* `backend/tests/test_mcp_server_resolver.py` — 8 tests: env fallback for `tenant_id=None`, explicit-label hit, missing label raises, blank label → default, blank label with no default → env, static_headers resolves `{{ env.KEY }}`, missing secret raises loud, oauth_2_1 raises `not yet implemented`.

### What's intentionally NOT in MCP-02

* **OAuth flow itself** — column exists, runtime rejects. That's MCP-03.
* **Dropdown picker** on the MCP Tool node config — today `mcpServerLabel` is a plain text field (matching the `integrationLabel` precedent on the AutomationEdge node). A picker is a refinement, not a blocker.
* **Fingerprint population** — the side table is empty. Written by MCP-06's drift-detection path.
* **Auto-invalidation of the tool-def cache** on registry CRUD. 5-minute staleness window remains. MCP-08 will close it properly via `notifications/tools/list_changed`.

---

## Sources

* [MCP spec — revision 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18) (transports, authorization, tools, resources, prompts, elicitation, security best practices)
* [MCP spec — revision 2025-11-25 changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)
* [RFC 8707 — Resource Indicators for OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc8707)
* [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
* [RFC 7591 — OAuth 2.0 Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)

Our files referenced: `backend/app/engine/mcp_client.py`, `backend/app/api/tools.py`, `backend/app/engine/node_handlers.py`, `backend/app/engine/react_loop.py`, `backend/app/config.py:9` (`mcp_server_url`), `backend/app/config.py:48` (`mcp_pool_size`), `backend/requirements.txt:20` (`mcp[cli]>=1.20.0`).
