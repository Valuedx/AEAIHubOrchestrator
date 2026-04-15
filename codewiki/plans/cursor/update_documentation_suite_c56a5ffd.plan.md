---
name: Update documentation suite
overview: Update all documentation files across codewiki/, docs/, and project root to reflect the context graph gap fixes, new graph API endpoints, Graph Explorer frontend page, and related changes across the codebase.
todos:
  - id: update-09-graph
    content: Update codewiki/09-graph-and-correlation.md with domain_id, enrichment edges, graph API, queries, negative penalty
    status: completed
  - id: update-05-search
    content: Update codewiki/05-search-hybrid-and-access.md with domain_id on scoring, negative penalty, correlation boost
    status: completed
  - id: update-api-md
    content: Update docs/API.md with /graph router and three endpoint docs
    status: completed
  - id: update-blueprint
    content: Update docs/TECHNICAL_BLUEPRINT.md package map, frontend summary, data model, last-reviewed
    status: completed
  - id: update-runbook
    content: Update docs/RUNBOOK.md migration table with 0014 and 0015
    status: completed
  - id: update-15-dashboard
    content: Update codewiki/15-dashboard-and-operator-workflows.md with Graph Explorer page
    status: completed
  - id: update-02-api
    content: Update codewiki/02-api-and-request-lifecycle.md router list
    status: completed
  - id: update-plan-md
    content: Update codewiki/PLAN.md article map anchor modules
    status: completed
  - id: update-readme
    content: Update README.md Alembic head and project structure
    status: completed
  - id: update-wiki-readme
    content: Update codewiki/README.md journey table
    status: completed
  - id: update-known-gaps
    content: Add Graph Explorer read-only caveat to codewiki/KNOWN_GAPS.md
    status: completed
isProject: false
---

# Documentation Update Plan

All changes stem from the context graph gap fixes and Graph Explorer feature. There are 12 files to update, organized by priority.

## Scope of changes to document

- `domain_id` added to `GraphEdge` model and threaded through all builder/query/ranker functions
- `ensure_edge` / `add_contradicts_edge` natural keys now include `domain_id`
- `persist_pattern_enrichment_edges` creates real graph edges (replaces virtual nodes)
- `get_pattern_subgraph` now supports `domain_id` filtering
- `get_graph_stats` uses `UNION ALL` for accurate deduplicated node counts
- `_graph_score_for_playbook` and `_identity_score_for_playbook` accept `domain_id`
- `_negative_penalty_for_playbook` scoring signal added to hybrid ranker
- New backend API router: `/graph` with `/neighbors`, `/subgraph/{type}/{id}`, `/stats`
- New frontend Graph Explorer page at `/graph-explorer` (three tabs: Stats, Subgraph, Neighbors)
- Shared `graph-constants.ts` extracted; `pattern-graph.tsx` refactored to use it
- Sidebar nav updated with `Waypoints` icon for Graph Explorer
- Alembic head advanced from `0013` to `0015_graph_edges_domain_id`
- Migration `0014_notifications_and_playbook_approval_policy` also exists (undocumented)

---

## Priority 1 -- Core graph documentation

### 1. [codewiki/09-graph-and-correlation.md](codewiki/09-graph-and-correlation.md)

Heaviest update. Current content is missing all graph enhancements.

- **Graph builder section (line 22-24):** Add `domain_id` to the edge field list. Document `persist_pattern_enrichment_edges` as a new builder function that replaces virtual enrichment nodes with persisted `GraphEdge` rows. Note that `ensure_edge` and `add_contradicts_edge` now include `domain_id` in their idempotency key.
- **Graph queries section (line 33):** Expand significantly. Document `get_neighbors` (BFS multi-hop, max depth 3, domain filtering), `get_pattern_subgraph` (now with `domain_id` param), `get_entity_subgraph` (generic BFS subgraph for any node type), and `get_graph_stats` (edge/node type counts using `UNION ALL` dedup).
- **Add new section:** "Graph HTTP API" documenting the `/graph` router with three endpoints: `GET /graph/neighbors`, `GET /graph/subgraph/{entity_type}/{entity_id}`, `GET /graph/stats`. Include query parameters and response shapes.
- **Hybrid use of graph row (line 109):** Add `_negative_penalty_for_playbook` to the key symbols. Note `domain_id` parameter on graph and identity scoring functions.
- **Code map table:** Add row for `api/v1/graph.py` (graph HTTP). Update `graph/builder.py` symbols to include `persist_pattern_enrichment_edges`, `_enrichment_node_id`. Update `graph/queries.py` symbols to include `get_entity_subgraph`, `get_graph_stats`.
- **Design decisions:** Add bullet: "Enrichment data persisted as real edges vs virtual nodes" -- why: enables BFS traversal and generic subgraph queries to reach enrichment data without special-case code. Tradeoff: more rows in `graph_edges`.
- **Example JSON output:** Update the graph edges example to include `domain_id` field.

### 2. [codewiki/05-search-hybrid-and-access.md](codewiki/05-search-hybrid-and-access.md)

- **Playbook hybrid ranking section (line 36-38):** Document `domain_id` parameter on `_graph_score_for_playbook` and `_identity_score_for_playbook`. Add `_negative_penalty_for_playbook` as a new scoring signal (contradiction edges + negative knowledge count, capped at 1.0). Note correlation co-occurrence boost in `_graph_score_for_playbook` via `CorrelationEdge`.
- **Weights list (line 38):** Already mentions negative penalty. Confirm breakdown example (line 104) includes `negative_penalty: 0.0` -- already present, no change needed.
- **Code map (line 135):** Add `_negative_penalty_for_playbook` to key symbols for hybrid ranker row.

---

## Priority 2 -- API and architecture docs

### 3. [docs/API.md](docs/API.md)

- **Router Index table (line 75-87):** Add row: `| /graph | graph | Graph traversal, subgraph visualization, aggregate statistics |`
- **Add new section after Drift:** "Graph" section documenting:
  - `GET /graph/neighbors` -- params: `node_type`, `node_id`, `edge_type?`, `max_depth?`, `domain_id?`. Returns array of neighbor objects with `node_type`, `node_id`, `edge_type`, `weight`, `direction`, `depth`.
  - `GET /graph/subgraph/{entity_type}/{entity_id}` -- params: `max_depth?`, `domain_id?`. Returns `{nodes: [...], edges: [...]}` for React Flow visualization.
  - `GET /graph/stats` -- params: `domain_id?`. Returns `{total_edges, edge_type_counts, node_type_counts}`.
- **Related Code Paths table:** Add `| Graph router | contextedge.api.v1.graph |`

### 4. [docs/TECHNICAL_BLUEPRINT.md](docs/TECHNICAL_BLUEPRINT.md)

- **Section 8 Backend Package Map (line 205):** The `Graph and patterning` row already exists. Verify it mentions `api/v1/graph.py` as part of the area. Update responsibility text to include "graph HTTP API, BFS traversal, aggregate stats".
- **Section 9 Frontend Summary (line 217):** Add `graph-explorer` to the representative route groups list. Mention shared `graph-constants.ts` and the React Flow-based Graph Explorer page.
- **Section 11 Logical Data Model (line 258):** In item 5 (Patterns and graph), note that `GraphEdge` now includes `domain_id` for domain-scoped graph queries.
- **Section 13 last-reviewed line (line 299):** Update to `2026-04-14. Codebase includes Alembic revisions through 0015_graph_edges_domain_id.`

### 5. [docs/RUNBOOK.md](docs/RUNBOOK.md)

- **Migration table (line 107-121):** Add two rows:
  - `| 0014_notifications_and_playbook_approval_policy | Notification tables and playbook approval policy |`
  - `| 0015_graph_edges_domain_id | domain_id column and composite index on graph_edges |`

---

## Priority 3 -- Frontend and navigation docs

### 6. [codewiki/15-dashboard-and-operator-workflows.md](codewiki/15-dashboard-and-operator-workflows.md)

- **Workflow table (line 11-17):** Add Graph Explorer to the "Investigate" row's "Main pages" column (alongside Evidence, Episodes, etc.).
- **Technical walkthrough:** Add new numbered item (between 5 and 6, or as 5b): "Graph Explorer provides interactive visualization" -- describe the three-tab page (Stats, Subgraph with React Flow, BFS Neighbors), how clicking a node re-centers the subgraph, and the backend API calls.
- **Code map table (line 102-118):** Add rows:
  - `| Graph Explorer page | frontend/src/app/(dashboard)/graph-explorer/page.tsx | GraphExplorerPage | Graph investigation |`
  - `| Graph visualization | frontend/src/components/graph/graph-subgraph.tsx | GraphSubgraph | Subgraph tab |`
  - `| Shared graph constants | frontend/src/components/graph/graph-constants.ts | nodeColors, edgeColors, NODE_TYPE_OPTIONS | All graph views |`
- **Note:** Mention that `pattern-graph.tsx` now imports from shared `graph-constants.ts` instead of inline maps.

### 7. [codewiki/02-api-and-request-lifecycle.md](codewiki/02-api-and-request-lifecycle.md)

- **Section 6 Router surface (line 23):** Add `graph` to the list of mounted routers: "...drift, execution, **graph**, etc."

### 8. [codewiki/PLAN.md](codewiki/PLAN.md)

- **Article map table, row 9 (line 26):** Add `api/v1/graph.py` to the "Doc + anchor modules" column.
- **Article map table, row 15 (line 32):** Add `frontend/src/app/(dashboard)/graph-explorer/page.tsx`, `frontend/src/components/graph/graph-constants.ts` to the "Doc + anchor modules" column.

---

## Priority 4 -- Root and meta docs

### 9. [README.md](README.md)

- **Development notes (line 101):** Update Alembic head from `0009_case_links` to `0015_graph_edges_domain_id`.
- **Project structure tree (line 122-123):** Update migration comment from `0001..0009` to `0001..0015`. Add `graph-explorer` comment to the frontend route groups if the tree is expanded.

### 10. [codewiki/README.md](codewiki/README.md)

- **Journey table, stage 6 (line 67):** Expand description: "Correlation edges link Jira ticket to Teams thread; graph scores playbooks; **Graph Explorer visualizes the context network**"

### 11. [docs/MIGRATIONS.md](docs/MIGRATIONS.md)

- No structural changes needed. The file is about the `0001` caveat and general guidance, not a migration list. The RUNBOOK has the definitive list.

### 12. [codewiki/KNOWN_GAPS.md](codewiki/KNOWN_GAPS.md)

- Add new section: "Graph Explorer is read-only" -- the Graph Explorer page provides visualization and traversal but does not yet support creating, editing, or deleting graph edges from the UI. All graph mutations happen through backend services (builder functions called from pattern discovery, playbook generation, contradiction scans, etc.).
