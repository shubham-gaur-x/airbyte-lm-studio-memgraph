# Graph Memory + Advanced Algorithms

Phases 21–25 add structured memory and graph intelligence to the meeting-memory pipeline.
Everything runs locally — no cloud, no external memory framework.

---

## 1. Overview: Three Memory Types

### Semantic Memory (`semantic_memory.py`)
Durable facts and inferred preferences extracted from meeting summaries via LM Studio.

- **Fact nodes** — persistent truths about people, projects, or topics. Confidence grows with each confirming meeting (starts at 0.3, maxes at 1.0).
- **Preference nodes** — inferred working preferences for attendees with ≥3 meetings in the graph (enough signal to generalize).
- **KNOWS edges** — weighted co-attendance count between people. Increments on every shared meeting.
- **INTERESTED_IN edges** — weighted person → topic interest. Increments when a person attends a meeting where a topic is discussed.

### Episodic Memory (`episodic_memory.py`)
Temporal chains and causal links between meetings, plus relevance decay.

- **PRECEDED_BY edges** — links each meeting to the most recent prior meeting sharing ≥1 attendee. Gives the graph a temporal spine per person/group.
- **CAUSED_BY edges** — if `follow_up_needed=True`, asks LM Studio whether this meeting explicitly continues a prior decision. If yes, links to the matching Decision or Meeting node.
- **relevance_weight** — starts at 1.0 on creation, decayed 5% per day (floor 0.1) by the nightly job. Used to weight graph context during memory retrieval.
- **MemorySession nodes** — every call to `POST /graph/memory/query` creates one, with `ACCESSED` edges to all nodes that contributed to the answer.

### Procedural Memory (`procedural_memory.py`)
Recurring workflows modelled as step-graphs.

- **Seeded procedures** — 6 known patterns: `sprint_planning`, `client_review`, `one_on_one`, `incident_response`, `project_kickoff`, `retrospective`.
- **FOLLOWS_PROCEDURE edges** — linked automatically when a meeting's topics and attendee count match a procedure's pattern. No LM call required.
- **Inferred procedures** — nightly job clusters unmatched meetings in the same community (≥5 meetings, ≥60% topic overlap) and creates `Procedure {is_inferred: true}` nodes.

---

## 2. Querying the Memory

### Natural Language Query
```bash
curl -X POST http://localhost:8000/graph/memory/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What did Alice discuss last week?"}'
```
Response:
```json
{
  "answer": "Alice discussed the roadmap and Q3 delivery in the sprint planning meeting on 2026-06-28.",
  "session_id": "...",
  "nodes_used": [{"id": "..."}, ...],
  "context_summary": {"people_found": 1, "topics_found": 2}
}
```

### Person Memory Profile
```bash
curl http://localhost:8000/graph/memory/person/alice@example.com
```
Returns: semantic facts, preferences, KNOWS connections, last 10 meetings by relevance_weight, PRECEDED_BY chain depth, matched procedures, algorithm scores.

### Recent Memory Sessions
```bash
curl http://localhost:8000/graph/memory/sessions
```

---

## 3. Algorithm Scores: How They're Computed

Five MAGE algorithms are run via `graph_algorithms.py` — the **only** module that contains MAGE `CALL` procedures.

| Algorithm | MAGE module | Stored on | What it means |
|---|---|---|---|
| PageRank | `pagerank.get()` | `node.pagerank_score` | Influence — who appears in many well-connected meetings |
| Community detection (fast) | `community_detection.get()` | `node.community_id` | Natural teams/clusters after each batch |
| Community detection (accurate) | `igraphalg.community_leiden()` | `node.community_id` | Nightly re-cluster with Leiden algorithm |
| Betweenness centrality | `betweenness_centrality.get()` | `node.betweenness_centrality` | Bridge connectors between otherwise-disconnected groups |
| Degree centrality | `degree_centrality.get()` | `node.degree_centrality` | Raw connection count |
| Weakly connected components | `weakly_connected_components.get()` | `node.wcc_id` | Organizational silos |

**Update triggers:**
- After each processed meeting batch (`run_fast_algorithms` — uses Louvain community detection)
- Nightly at 02:00 (`run_full_algorithms` — uses Leiden community detection, more accurate)

**API endpoints:**
- `GET /graph/insights/influential?label=Person&limit=10` — top nodes by PageRank
- `GET /graph/insights/communities` — all community_ids with member node IDs
- `GET /graph/insights/bridges?limit=10` — top nodes by betweenness centrality
- `GET /graph/insights/node/{node_id}` — all algorithm scores for one node

---

## 4. Procedure Matching and Discovery

### Known Procedures (matched automatically during ingestion)
Each meeting is checked against 6 patterns in `procedural_memory.KNOWN_PROCEDURE_PATTERNS`.
Matching is pure Python — no LM call, no async. A meeting can match multiple procedures.

Example: a sprint retrospective matches both `sprint_planning` (topics: "backlog", "sprint") and `retrospective` (topics: "what went well", "improvements").

When matched: `(Meeting)-[:FOLLOWS_PROCEDURE {confidence: 0.8}]->(Procedure)` is MERGED and `Procedure.occurrence_count` incremented.

### Inferred Procedures (discovered nightly)
The nightly `discover_procedures` job (02:45):
1. Finds meetings not yet linked to any Procedure, grouped by the `community_id` of their attendees.
2. Within each community group, clusters meetings with ≥60% Jaccard topic overlap.
3. If a cluster has ≥5 meetings, creates a `Procedure {is_inferred: true}` and links all meetings.

**API endpoints:**
- `GET /graph/procedures` — all procedures with occurrence count and ordered steps
- `GET /graph/procedures/{name}` — detailed view: steps + matched meetings

---

## 5. MCP Access

The Memgraph MCP server (`http://localhost:8000/mcp/`) exposes all graph properties to Claude Desktop and agents. Since `pagerank_score`, `community_id`, and `betweenness_centrality` are now node properties, Claude Desktop can answer questions like:

> "Who are the most influential people in my meetings?"
> MATCH (p:Person) WHERE p.pagerank_score IS NOT NULL RETURN p.name, p.pagerank_score ORDER BY p.pagerank_score DESC LIMIT 5

> "Which community does Alice belong to, and who else is in it?"
> MATCH (p:Person {email: "alice@example.com"}) WITH p.community_id AS cid MATCH (q:Person {community_id: cid}) RETURN q.name

No extra configuration needed — the MCP server already has `MCP_READ_ONLY=false` and write access to run these queries.

---

## 6. Schema: New Node Types and Edges

```
Existing nodes (with new properties added)
──────────────────────────────────────────
Meeting  ── relevance_weight: float (1.0 on creation, decays 5%/day)
Person   ── pagerank_score, community_id, betweenness_centrality,
            degree_centrality, wcc_id
Topic    ── pagerank_score, community_id

New node types (Phases 21-25)
─────────────────────────────
Fact          {id, text, confidence, source_count, created_at, updated_at}
Preference    {id, category, value, confidence, created_at, updated_at}
Procedure     {id, name, description, match_pattern, is_inferred,
               occurrence_count, created_at, updated_at}
ProcedureStep {id, name, description, order, created_at}
MemorySession {id, query_text, answer_text, nodes_accessed, created_at}

New edge types (Phases 21-25)
─────────────────────────────
HAS_FACT         (Meeting | Person | Topic) → Fact
PREFERS          Person → Preference
KNOWS            Person → Person          {weight: int}
INTERESTED_IN    Person → Topic           {weight: int}
PRECEDED_BY      Meeting → Meeting        {gap_days: int}
CAUSED_BY        Meeting → Meeting | Decision  {confidence: float}
FOLLOWS_PROCEDURE Meeting → Procedure    {confidence: float}
HAS_STEP         Procedure → ProcedureStep
NEXT_STEP        ProcedureStep → ProcedureStep  {condition: str|null}
ACCESSED         MemorySession → any node

ASCII diagram
─────────────

  Person ──[KNOWS]──────────────► Person
    │
    ├──[ATTENDED]──► Meeting ──[PRECEDED_BY]──► Meeting
    │                   │
    │                   ├──[DISCUSSED]──► Topic
    │                   │                   └──[HAS_FACT]──► Fact
    │                   ├──[PRODUCED]──► Decision
    │                   ├──[FOLLOWS_UP]──► ActionItem
    │                   ├──[HAS_FACT]──► Fact
    │                   └──[FOLLOWS_PROCEDURE]──► Procedure
    │                                                └──[HAS_STEP]──► ProcedureStep
    │                                                                      └──[NEXT_STEP]──►...
    ├──[PREFERS]──► Preference
    └──[INTERESTED_IN]──► Topic

  MemorySession ──[ACCESSED]──► Meeting | Person | Topic | Fact | ...
```
