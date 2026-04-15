# Feature Roadmap

Gap analysis comparing AEAIHubOrchestrator against top DAG workflow builders for agentic AI: **LangGraph**, **Dify**, **n8n**, **Flowise**, **CrewAI**, and **Rivet**.

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
| **P2** | 11 | Advanced Memory (semantic, entity) | CrewAI, LangGraph | **Done** |
| **P2** | 12 | Data Transformation Nodes | n8n, Dify | Planned |
| **P2** | 13 | Evaluation / Testing Framework | LangSmith, Dify | Planned |
| **P2** | 14 | RBAC / Team Collaboration | Dify | Planned |
| **P2** | 15 | Marketplace / Community Nodes | n8n, Flowise | Planned |
| **P2** | 16 | Multi-Way Branching (Switch Node) | n8n, Dify | Planned |
| **P3** | 17 | Canvas UX (auto-layout, groups, comments) | Rivet, n8n | Planned |
| **P3** | 18 | API Playground / Embed Widgets | Dify, Flowise | Planned |
| **P3** | 19 | Environment / Variable Management UI | n8n, Dify | Planned |
| **P3** | 20 | Execution Analytics Dashboard | n8n, Dify | Planned |

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

#### 17. Canvas UX Enhancements — Planned

> Rivet and n8n have advanced canvas features.

Missing features:
- Auto-layout algorithm for graphs
- Group/frame nodes for visually organizing workflow sections
- Comment/annotation nodes
- Copy-paste of node groups across workflows
- Minimap or overview panel for large graphs

#### 18. API Playground / Chatbot Embed — Planned

> Dify provides a built-in API playground for testing, plus embeddable chatbot widgets. Flowise offers one-click API deployment + embed widgets.

No equivalent exists beyond the bridge/client pattern. A test console in the UI for sending payloads and viewing responses, plus embeddable chat widgets, would improve developer experience.

#### 19. Environment / Variable Management UI — Planned

> n8n and Dify have UIs for managing environment variables and workflow-level variables.

`{{ env.* }}` resolution works, but there's no UI to view, create, or edit environment variables or workflow-level variables. This is closely related to gap #4 (Credential Management UI) and could be addressed together.

#### 20. Execution Analytics Dashboard — Planned

> n8n and Dify provide execution analytics with success rates, duration charts, and cost tracking.

No searchable/filterable audit log across all workflow executions. No execution analytics (success rates, average duration, cost over time). Adding aggregate metrics and a dashboard view would support operations and capacity planning.

---

## Current strengths

Features where AEAIHubOrchestrator is competitive or ahead:

| Strength | Details |
|----------|---------|
| **A2A Protocol** | First-class inter-agent delegation that most competitors lack natively |
| **NLP Nodes** | Dedicated Intent Classifier (hybrid scoring) and Entity Extractor (rule-based + LLM fallback) with optional embedding caching |
| **Advanced Memory** | Normalized conversation storage, rolling summaries, semantic/episodic/entity memory, profile-driven prompt assembly, and inspection APIs |
| **MCP Integration** | Extensible tool discovery via a standard protocol |
| **HITL + Pause/Resume/Cancel** | Richer operator control than most visual builders |
| **Checkpointing + Debug Replay** | On par with LangGraph's checkpointing |
| **Deterministic Mode** | Unique feature for reproducible testing (sets LLM temperature to 0) |
| **Bridge Pattern** | Clean separation for embedding into parent apps |
| **Version History + Rollback** | On par with Dify |
| **Portable Architecture** | Easier to deploy than LangGraph Cloud; fully self-contained |
| **RAG / Knowledge Base** | Pluggable vector stores, multiple embedding providers, four chunking strategies |
