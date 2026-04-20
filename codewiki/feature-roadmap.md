# Feature Roadmap

Gap analysis comparing AEAIHubOrchestrator against top DAG workflow builders for agentic AI: **LangGraph**, **Dify**, **n8n**, **Flowise**, **CrewAI**, and **Rivet**.

> **Skip to:** [Recent releases](#recent-releases) · [Priority matrix](#priority-matrix) · [Pending backlog](#pending-backlog)

---

## Recent releases

### Sprint 2A — Developer Velocity (DV-01 … DV-07) — done

| # | Feature | Commit |
|---|---------|--------|
| DV-01 | Data pinning — short-circuit dispatch on a pinned node output | `1e7994b` |
| DV-02 | Test a single node in isolation (uses DV-01 pins for upstream context) | `47ce5f8` |
| DV-03 | Sticky notes on the canvas (non-executable annotations) | `dd0b510` |
| DV-04 | Expression helpers library — 45 new `safe_eval` functions | `8899574` |
| DV-05 | Duplicate workflow with copy-suffix collision handling | `625adbc` |
| DV-06 | Hotkey cheatsheet (`?`) + Shift+S / `1` / Tab shortcuts | `dd0b510` |
| DV-07 | Active / Inactive toggle — Schedule Triggers stop firing on inactive workflows | `625adbc` |

See [Developer Workflow](dev-workflow.md) for each feature's UI flow, storage semantics, and test coverage.

### Sprint 2B — MCP Maturity (MCP-01 … MCP-02) — done

| # | Feature | Commit |
|---|---------|--------|
| MCP-01 | Audit current client vs. 2025-06-18 spec + ranked gap list | `091403c` |
| MCP-02 | Per-tenant MCP server registry (`tenant_mcp_servers`) + resolver + dialog | `f2327e6` |

See [MCP Audit](mcp-audit.md) for findings and the per-tenant registry design.

---

## Pending backlog

### MCP Maturity — next tickets (from [mcp-audit.md §6](mcp-audit.md))

Ranked by impact-in-our-context:

| # | Title | Why it matters |
|---|-------|----------------|
| MCP-03 | OAuth 2.1 resource-server client | Required to talk to any spec-compliant hosted MCP service. Registry already has `auth_mode='oauth_2_1'` column. |
| MCP-04 | HITL confirmation gate for destructive tools | Biggest production-risk gap today — we ignore `destructiveHint`. |
| MCP-05 | Structured tool output + `outputSchema` validation | Cheap; fixes lossy result parsing. |
| MCP-06 | Tool-definition fingerprinting + drift detection | Mitigates tool-poisoning. Side table already exists from migration 0019. |
| MCP-07 | Elicitation support (`elicitation/create` → HITL suspend) | Unlocks interactive MCP tools. |
| MCP-08 | `notifications/tools/list_changed` subscription + cache invalidation | Closes the 5-minute staleness window. |
| MCP-09 | `HTTP DELETE` session release on shutdown + resumability integration test | Politeness + confidence. |
| MCP-10 | Bump protocol version to `2025-11-25` | Non-breaking; picks up OIDC discovery, scope-minimization, icons. |

Deferred: sampling (MCP-11), resources / prompts primitives (MCP-12). See [mcp-audit.md §6](mcp-audit.md) for full rationale.

---

## Priority matrix

| Priority | # | Feature | Competitive Pressure | Status |
|----------|---|---------|---------------------|--------|
| **P0** | 1 | RAG / Knowledge Base / Vector Store | Dify, Flowise, n8n | **Done** |
| **P0** | 2 | Code Execution Node | Dify, n8n, LangGraph | **Done** |
| **P0** | 3 | Integration Ecosystem (native connectors) | n8n (400+), Dify, Flowise | **Partial** |
| **P0** | 4 | Credential Management UI | n8n, Dify | **Done** |
| **P1** | 5 | In-Process Multi-Agent Patterns | CrewAI, LangGraph, AutoGen | Planned |
| **P1** | 6 | Subgraphs / Nested Workflows | LangGraph, Dify | **Done** |
| **P1** | 7 | Cyclic Graph Support | LangGraph | Planned |
| **P1** | 8 | Built-in Observability Dashboard | Dify, LangSmith | Planned |
| **P1** | 9 | Per-Node Error Handling & Retry | n8n | Planned |
| **P1** | 10 | Dynamic Fan-Out Map-Reduce | LangGraph | Planned |
| **P1** | 21 | MCP Client Maturity (OAuth 2.1, elicitation, structured output, drift detection) | Claude Desktop, Cursor, 2026 ecosystem | **Partial** |
| **P1** | 22 | Tool Trust & Safety UX (annotation-driven destructive-tool gate) | Spec MUST; 2025 OWASP-AI flagged | Planned |
| **P2** | 11 | Advanced Memory (semantic, entity) | CrewAI, LangGraph | **Done** |
| **P2** | 12 | Data Transformation Nodes | n8n, Dify | Planned |
| **P2** | 13 | Evaluation / Testing Framework | LangSmith, Dify | Planned |
| **P2** | 14 | RBAC / Team Collaboration | Dify | Planned |
| **P2** | 15 | Marketplace / Community Nodes | n8n, Flowise | Planned |
| **P2** | 16 | Multi-Way Branching (Switch Node) | n8n, Dify | Planned |
| **P3** | 17 | Canvas UX (auto-layout, groups, comments) | Rivet, n8n | **Partial** |
| **P3** | 18 | API Playground / Embed Widgets | Dify, Flowise | Planned |
| **P3** | 19 | Environment / Variable Management UI | n8n, Dify | **Partial** |
| **P3** | 20 | Execution Analytics Dashboard | n8n, Dify | Planned |
| **P3** | 23 | Real-time Collaboration (presence, comments) | Dify, Figma-class | Planned |
| **P3** | 24 | Workflow Authoring Copilot (NL → draft) | Dify AI copilot, 2026 trend | Planned |
| **P3** | 25 | MCP Server Catalogue (curated + verified fingerprints) | Claude Desktop server registry | Planned |

---

## Detailed descriptions

### P0 — Critical gaps (table-stakes for competitive parity)

#### 1. RAG / Knowledge Base / Vector Store — Done

> Dify, Flowise, and n8n all have built-in knowledge base nodes with document ingestion, chunking, embedding, and retrieval. Dify supports 6+ vector store backends.

**Implemented:** Full RAG pipeline with pluggable vector stores (pgvector, FAISS), multiple embedding providers (OpenAI, Google GenAI, Google Vertex AI), four chunking strategies (recursive, token, markdown, semantic), async document ingestion, Knowledge Retrieval workflow node, and management UI.

See [RAG & Knowledge Base](rag-knowledge-base.md) for full documentation.

#### 2. Code Execution Node — Done

> Dify has sandboxed Python/JavaScript code nodes. n8n has a Code node. LangGraph nodes are arbitrary Python functions.

**Implemented:** Sandboxed Python code execution node ("Code") running user code in a separate subprocess with multiple security layers: restricted builtins (no `open`/`exec`/`eval`), import whitelist (30 safe stdlib modules), per-node timeout (max 120 s), 1 MB output cap, and clean environment (no app secrets). The frontend config panel renders a monospace code editor with tab support. Data flows in via an `inputs` dict and out via an `output` variable.

See [Node Types — Code](node-types.md) for full documentation.

#### 3. Integration Ecosystem / Pre-built Connectors — Partial

> n8n has 400+ pre-built nodes (Slack, Gmail, Google Sheets, Airtable, databases, CRMs, etc.). Dify and Flowise have native integrations for common services.

**Progress:** The **Notification node** adds native connectors for 8 channels: Slack (webhook), Microsoft Teams (webhook), Discord (webhook), Telegram (Bot API), WhatsApp (Meta Cloud API), PagerDuty (Events v2), Email (SendGrid, Mailgun, SMTP), and generic webhooks. All channels support three config value sources (static, vault secrets, runtime expressions) and Jinja2 message templating. See [Notification Guide](notification-guide.md).

**Remaining:** Database query nodes, file storage (S3, GCS), CRM connectors, Google Sheets, and other SaaS-specific nodes are not yet implemented. MCP tools remain the extensibility mechanism for uncommon integrations.

#### 4. Credential / Secret Management UI — Done

> n8n and Dify have full credential management UIs (add, test, reuse across workflows).

**Implemented:** Full REST API (`/api/v1/secrets`) for CRUD on tenant secrets with Fernet encryption. Frontend `SecretsDialog` accessible from the toolbar (KeyRound icon) with create, update, delete views. Secret values are never exposed after creation — only `{{ env.KEY_NAME }}` references are shown. Fixed the missing `get_tenant_secret` function so `{{ env.* }}` resolution now works at runtime.

See [API Reference](api-reference.md) and [Security](security.md) for details.

---

### P1 — Major gaps (strong differentiators in competing tools)

#### 5. In-Process Multi-Agent Patterns — Planned

> LangGraph supports supervisor/worker patterns and multi-agent swarms within a single graph. CrewAI has role-based teams with sequential, hierarchical, and consensus-driven coordination. AutoGen has structured multi-agent conversations.

We have A2A delegation (remote agents) but no native in-process multi-agent patterns (supervisor, swarm, debate, voting, hierarchical delegation within one workflow). Implementing supervisor and team coordination nodes would close this gap.

#### 6. Subgraphs / Nested Workflows — Done

> LangGraph has composable subgraphs (a compiled graph used as a node inside a larger graph). Dify supports nested workflow calls.

**Implemented:** Sub-Workflow logic node (`sub_workflow`) that executes another saved workflow as a single step. Child workflows run synchronously inline, creating a separate `WorkflowInstance` linked via `parent_instance_id` / `parent_node_id`. Input mapping via `safe_eval` expressions builds the child's `trigger_payload`; output filtering restricts which child node outputs are returned. Version policy: `latest` (live definition) or `pinned` (specific snapshot version). Recursion protection via `_parent_chain` prevents cycles and enforces configurable `maxDepth` (default 10, max 20). Cancellation cascades from parent to child instances. Frontend includes custom `WorkflowSelect`, `InputMappingEditor`, and `OutputNodePicker` widgets, canvas version-policy badge, and drill-down child execution logs.

See [Node Types — Sub-Workflow](node-types.md) for full config reference.

#### 7. Cyclic Graph Support — Planned

> LangGraph explicitly supports cyclic state machines — agents can loop back to previous nodes based on conditions. This is its core selling point.

We enforce DAG-only (Kahn cycle detection rejects cycles). Loop/ForEach are workarounds, not true cyclic graph support. This limits the expressiveness of agent reasoning loops. Supporting cycles would require rethinking the execution engine to handle state machines.

#### 8. Built-in Observability Dashboard — Planned

> Dify has built-in token counts, node-level latency, cost tracking, and execution analytics in the UI. LangGraph has deep integration with LangSmith.

We have optional Langfuse + SSE logs, but no in-app cost tracking, token counting, latency metrics, or analytics dashboard. Adding per-node timing, token usage, and cost estimation to execution logs and a summary view in the UI would address this.

#### 9. Per-Node Error Handling & Retry — Planned

> n8n has per-node "Retry on Fail" configuration (count, delay), dedicated Error Trigger workflows, and fallback paths.

We have instance-level retry from a failed node but no per-node retry policies, error fallback edges, or error trigger workflows. Adding retry config to the node schema and fallback edge support to the DAG runner would close this gap.

#### 10. Dynamic Fan-Out Map-Reduce — Planned

> LangGraph's `Send()` API dynamically spawns parallel workers based on runtime state and reduces results.

ForEach runs downstream nodes per-element but lacks dynamic fan-out where the number of parallel branches is determined at runtime by data shape, not graph structure. True parallel map-reduce with configurable concurrency limits would be valuable.

#### 21. MCP Client Maturity — Partial

> The MCP ecosystem standardized OAuth 2.1 resource-server auth in March 2025, added structured tool output and elicitation in 2025-06-18, and is shifting to OIDC discovery + scope-minimization in 2025-11-25. Claude Desktop, Cursor, and Continue all ship most of these; we ship tools/call + tools/list only.

**Progress (MCP-01, MCP-02 done):** Full audit of our client against the 2025-06-18 spec lives in [MCP Audit](mcp-audit.md). Per-tenant MCP server registry (MCP-02) replaces the single-server env-var config with `tenant_mcp_servers` — each row captures URL, auth mode (`none` / `static_headers` / `oauth_2_1`), and optional `{{ env.KEY }}` header placeholders resolved through the Secrets vault. Session pool + list-tools cache are now keyed by `(tenant, server)` so tenants can't share warm connections.

**Remaining (MCP-03..MCP-10):** OAuth 2.1 resource-server client (MCP-03; column exists, runtime raises today) · structured tool output + outputSchema validation (MCP-05) · elicitation → HITL suspend (MCP-07) · tool-definition fingerprint drift (MCP-06; empty side table already exists from migration 0019) · `notifications/tools/list_changed` cache invalidation (MCP-08) · `HTTP DELETE` session release + resumability test (MCP-09) · protocol-version bump to 2025-11-25 (MCP-10).

#### 22. Tool Trust & Safety UX — Planned

> The MCP spec declares that "applications **SHOULD** present confirmation prompts to the user for operations, to ensure a human is in the loop" and "clients **MUST** consider tool annotations to be untrusted unless they come from trusted servers." Today we ignore `destructiveHint` / `readOnlyHint` / `idempotentHint` / `openWorldHint` entirely. 2025-era advisories (tool poisoning, rug-pull via description mutation, confused-deputy via token passthrough) add pressure.

A `delete_customer` tool is currently one config field away from firing unattended on a scheduled trigger. Closing this means:

1. Surface tool annotations on the MCP Tool node at design time so workflow authors see the risk.
2. Gate destructive tool calls (`destructiveHint: true` AND the server is not on a trust-list) behind a HITL suspend when the parent workflow is not marked autonomous. Piggybacks on the existing `suspended_reason` / resume path.
3. Show the operator the tool call's inputs *before* it fires (spec: prevents accidental exfiltration).
4. Pair with MCP-06 fingerprint drift so a mutated tool description re-prompts for consent.

Together with MCP-06, this is the largest production-risk improvement available without new infrastructure.

---

### P2 — Moderate gaps (nice-to-have, common in mature tools)

#### 11. Advanced Memory Systems — Done

> CrewAI has short-term, long-term, and entity memory. LangGraph has typed state with reducers and cross-thread memory stores.

**Implemented:** Advanced Memory v1 hard cutover. Conversation transcripts are normalized into `conversation_messages`; `conversation_sessions` now stores summary metadata only. `memory_profiles` define tenant/workflow memory policy, `memory_records` store semantic and episodic memories across `session`, `workflow`, `tenant`, and `entity` scopes, and `entity_facts` stores relational entity memory with last-write-wins semantics. Agent/ReAct prompts are now turn-aware and token-budgeted, router/classifier history packing is shared, and operators can inspect the exact memories used by a run through `/api/v1/memory/instances/{instance_id}/resolved`.

See [Memory Management](memory-management.md) for the full design.

#### 12. Data Transformation Nodes — Planned

> n8n has Set, Split, Merge, Aggregate, Filter, Sort nodes. Dify has Template Transform.

No dedicated JSON transform, filter, aggregate, or template transform nodes. Users must resort to `safe_eval` expressions or code execution for data manipulation. Purpose-built transformation nodes would improve usability for non-technical users.

#### 13. Evaluation / Testing Framework — Planned

> LangSmith and Dify both offer prompt evaluation, A/B testing, and dataset-based testing.

No prompt evaluation, A/B testing, dataset-based testing, or regression testing for workflows. An evaluation framework that can run workflows against test datasets and compare outputs would be valuable for quality assurance.

#### 14. RBAC / Team Collaboration — Planned

> Dify has RBAC, team workspaces, and role-based access.

We have tenant isolation but no intra-tenant roles, team workspaces, or collaboration features (commenting, sharing, co-editing). Adding role-based permissions (viewer, editor, admin) within a tenant would support team use cases.

#### 15. Marketplace / Community Nodes — Planned

> n8n has a community node marketplace. Flowise has a component marketplace.

Template gallery exists but no community-contributed node types or workflow marketplace. A plugin/extension system for custom node types, combined with a sharing mechanism, would enable ecosystem growth.

#### 16. Multi-Way Branching (Switch Node) — Planned

> n8n and Dify support multi-way routing with switch/case patterns.

Only binary true/false Condition nodes exist. No multi-way switch/case routing. LLM Router helps but is non-deterministic and costs tokens. A deterministic Switch node with N output branches based on expression matching would fill this gap.

---

### P3 — Lower priority (polish and advanced features)

#### 17. Canvas UX Enhancements — Partial

> Rivet and n8n have advanced canvas features.

**Shipped (Sprint 2A):** Comment/annotation nodes via **Sticky Notes** (DV-03) with six preset colours, non-executable, filtered at parse time. **Hotkey cheatsheet** (DV-06) surfaces every canvas shortcut behind `?` with shared `isTextEditingTarget` guard. Minimap + pannable zoom exists on the main canvas. **Duplicate workflow** (DV-05) with collision-safe copy-suffix handling.

**Remaining:**
- Auto-layout algorithm for graphs
- Group/frame nodes for visually organising workflow sections
- Copy-paste of node groups across workflows (today: save as template, load as starter)
- Multi-select + bulk-edit on the canvas

#### 18. API Playground / Chatbot Embed — Planned

> Dify provides a built-in API playground for testing, plus embeddable chatbot widgets. Flowise offers one-click API deployment + embed widgets.

No equivalent exists beyond the bridge/client pattern. A test console in the UI for sending payloads and viewing responses, plus embeddable chat widgets, would improve developer experience.

#### 19. Environment / Variable Management UI — Partial

> n8n and Dify have UIs for managing environment variables and workflow-level variables.

**Shipped:** `{{ env.KEY }}` resolution works end-to-end. The **Secrets dialog** (KeyRound icon) handles Fernet-encrypted tenant-scoped env vars. The **Tenant Integrations dialog** (Bot icon) and **MCP Servers dialog** (Globe icon, MCP-02) both surface `{{ env.* }}` placeholders in their connection configs so operators register a secret once and reference it from multiple places.

**Remaining:** No UI for workflow-scoped variables (today: pass via `trigger_payload` or derive in an early `safe_eval` node). No "variable autocomplete" in expression fields.

#### 20. Execution Analytics Dashboard — Planned

> n8n and Dify provide execution analytics with success rates, duration charts, and cost tracking.

No searchable/filterable audit log across all workflow executions. No execution analytics (success rates, average duration, cost over time). Adding aggregate metrics and a dashboard view would support operations and capacity planning.

#### 23. Real-time Collaboration (presence, comments) — Planned

> Dify has team workspaces but no real-time co-editing. Figma-class multiplayer on the canvas is the direction the category is heading.

Closely related to #14 (RBAC / Team Collaboration) but distinct: RBAC is *static* — who may edit. Collaboration is *live* — two editors see each other's cursors, per-node comment threads that persist across saves, "someone else is editing this node" locks. Requires a WebSocket broadcast layer (the SSE path is one-way) and per-node presence/comment storage. Would layer cleanly on top of the `workflow_snapshots` history — threads could anchor to a node id + version.

#### 24. Workflow Authoring Copilot (NL → draft workflow) — Planned

> Dify's AI Agent copilot can draft and refine workflows from a natural-language prompt. Anthropic's Claude Code has begun embedding this pattern for tool-chain generation.

Today, authoring is drag-and-drop only. A copilot that accepts "build me a classifier that routes refund requests to AE and everything else to an LLM reply" and produces a draft graph would shorten the ramp for non-builder users. Architecture: a tenant-scoped LLM node chain that reads the current `node_registry.json`, emits a candidate `graph_json`, validates against `config_validator`, then opens it on the canvas as an unsaved draft. High-leverage combined with our existing **Test single node** (DV-02) probe — the copilot can iterate on a node's config without end-to-end runs.

#### 25. MCP Server Catalogue — Planned

> Claude Desktop ships a curated list of official MCP servers (filesystem, brave-search, github, …). Cursor and Continue extend similar lists. Each is one click to install.

Natural pairing with MCP-02 (the registry is there; seed it). A catalogue UI that lists well-known public MCP servers, each with a verified fingerprint (pairs with MCP-06 drift detection), one-click "Add to tenant" action, and an indicator if the server requires OAuth (pairs with MCP-03). Lower-risk than a general node marketplace (#15) because each entry's surface area is bounded by the MCP protocol.

---

## Current strengths

Features where AEAIHubOrchestrator is competitive or ahead:

| Strength | Details |
|----------|---------|
| **A2A Protocol** | First-class inter-agent delegation that most competitors lack natively |
| **NLP Nodes** | Dedicated Intent Classifier (hybrid scoring) and Entity Extractor (rule-based + LLM fallback) with optional embedding caching |
| **Advanced Memory** | Normalized conversation storage, rolling summaries, semantic/episodic/entity memory, profile-driven prompt assembly, and inspection APIs |
| **MCP Integration** | Per-tenant server registry (MCP-02) with auth-mode discriminator + `{{ env.KEY }}` vault indirection. Remaining gaps (OAuth, elicitation, structured output, drift detection) are named in [MCP Audit](mcp-audit.md) and scheduled as MCP-03..MCP-10. |
| **HITL + Pause/Resume/Cancel** | Richer operator control than most visual builders |
| **Checkpointing + Debug Replay** | On par with LangGraph's checkpointing |
| **Deterministic Mode** | Unique feature for reproducible testing (sets LLM temperature to 0) |
| **Bridge Pattern** | Clean separation for embedding into parent apps |
| **Version History + Rollback** | On par with Dify |
| **Portable Architecture** | Easier to deploy than LangGraph Cloud; fully self-contained |
| **RAG / Knowledge Base** | Pluggable vector stores, multiple embedding providers, four chunking strategies |
| **AutomationEdge Async-External Integration** | Pattern A webhook + Pattern C Beat poll both resume through the same `finalize_terminal` path. Diverted pause-the-clock timeout model. Tenant-scoped connection defaults via `tenant_integrations`. |
| **Developer Velocity (Sprint 2A)** | Pin node outputs and test a single node in isolation (DV-01 + DV-02) — the edit→run→inspect loop no longer requires end-to-end runs. Plus 45 `safe_eval` expression helpers (DV-04), sticky notes (DV-03), duplicate workflow (DV-05), hotkey cheatsheet (DV-06), and active/inactive toggle (DV-07). See [Developer Workflow](dev-workflow.md). |
| **Tenant-scoped MCP Registry (MCP-02)** | Per-tenant MCP server registration with session-pool isolation across tenants and a forward-declared fingerprint side table for drift detection. Supports `none` and `static_headers` auth modes today; registry schema ready for OAuth without migration churn. |
