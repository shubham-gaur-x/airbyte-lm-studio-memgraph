# Design: Graph Memory + Advanced Algorithms

**Date:** 2026-06-30
**Author:** Shubham Gaur (planned with Claude)
**Status:** Approved — implementation not yet started

---

## Problem

The Memgraph graph stores rich meeting data but treats it as a passive archive.
Two capabilities are missing:

1. **Graph intelligence** — no computed metrics tell us who is most influential,
   which people cluster together, or who bridges otherwise-disconnected groups.
   Those signals live latent in the edges but are never surfaced.

2. **Structured memory** — there is no distinction between *facts the system
   knows* (semantic), *events the system experienced* (episodic), and *processes
   the system knows how to run* (procedural). Everything is one flat collection
   of Meeting nodes with no temporal chains, no durable knowledge, and no model
   of recurring workflows.

## Non-Goals (this phase)

- No Enterprise license, no online/dynamic algorithms — static MAGE algorithms
  only, triggered on demand and on schedule
- No Mem0, Cognee, or any external memory framework — custom Cypher + MAGE + MCP
- No cloud LLM — LM Studio throughout

## Key Constraint: Image Already Has MAGE

`memgraph/memgraph-platform` (current image) bundles MAGE. **No image change
needed.** MAGE algorithms are available via `CALL module.procedure()` in Cypher
right now. This is also why the CLAUDE.md rule below matters: anyone writing
Cypher might accidentally embed CALL procedures inline. Don't.

---

## Architecture

### 1 — Advanced MAGE Algorithms (`graph_algorithms.py`)

Five static algorithms, all open-source, run against the full graph:

| Algorithm | MAGE module | Stored as | Value to this project |
|---|---|---|---|
| PageRank | `pagerank.get()` | `node.pagerank_score` | Most influential people + topics |
| Community detection | `leiden_community_detection.get()` | `node.community_id` | Natural teams/clusters |
| Betweenness centrality | `betweenness_centrality.get()` | `node.betweenness_centrality` | Bridge connectors between groups |
| Degree centrality | `degree_centrality.get()` | `node.degree_centrality` | Raw connectivity count |
| Weakly connected components | `weakly_connected_components.get()` | `node.wcc_id` | Organizational silos |

Results are written back to node properties immediately via the same Cypher
`YIELD ... SET` pattern — no second round-trip.

**Two trigger points (both → same functions):**
- Event-driven: called at the end of each processed batch in `graph_builder.py`
  (fast, scoped to what changed). Uses `community_detection` (Louvain, O(n log n))
  for speed.
- Nightly: APScheduler job at 02:00 local time in `main.py`. Uses
  `leiden_community_detection` (more accurate, runs overnight). Also recomputes
  all five algorithms from scratch over the full graph.

New query functions in `memgraph_client.py` expose stored scores:
`get_influential_nodes`, `get_community_members`, `get_bridge_nodes`,
`get_similar_nodes` (on-demand `node_similarity.jaccard` call, not batch).

New API endpoints: `/graph/insights/influential`, `/graph/insights/communities`,
`/graph/insights/bridges`, `/graph/insights/similar/{node_id}`.

### 2 — Semantic Memory (`semantic_memory.py`)

**What:** Durable facts and preferences, distinct from transient meeting summaries.

New node types:
- `Fact {id, text, confidence, source_count, created_at, updated_at}` —
  persistent knowledge extracted from meeting summaries via LM Studio. Confidence
  increases as more meetings confirm the same fact.
- `Preference {id, category, value, confidence, created_at, updated_at}` —
  inferred characteristics of a person (e.g. "prefers async communication",
  "always attends sprint planning").

New edge types:
- `HAS_FACT` (Person | Topic | Meeting → Fact)
- `PREFERS` (Person → Preference)
- `KNOWS {weight: int}` (Person → Person) — weight = co-attendance count,
  incremented on each shared meeting. `MERGE` not `CREATE` so it accumulates.
- `INTERESTED_IN {weight: int}` (Person → Topic) — weight = meetings where
  person attended and topic was discussed.

**Three functions in `semantic_memory.py`:**
1. `extract_facts(meeting, meeting_id)` — short LM Studio call: "Extract 3-5
   durable facts from this meeting summary. Facts only, not events." MERGE Fact
   nodes + HAS_FACT edges to the Meeting and to any mentioned Person.
2. `infer_preferences(meeting, meeting_id)` — one LM Studio call per attendee
   only if they have ≥3 meetings in the graph (enough signal). MERGE Preference
   nodes + PREFERS edges.
3. `strengthen_relationships(meeting, meeting_id)` — no LM call: pure Cypher.
   For each pair of attendees: MERGE (p1)-[:KNOWS {weight:1}]->(p2) then
   `SET r.weight = r.weight + 1`. Same for Person → Topic INTERESTED_IN.

**Nightly consolidation job `consolidate_semantic()`:** Find Facts with
`source_count` reaching multiples of 3 (3, 6, 9…) and raise their confidence.
Facts that appear in 10+ meetings get `confidence = 1.0` (considered certain).

### 3 — Episodic Memory (`episodic_memory.py`)

**What:** Temporal chains and causality between meetings, plus memory decay.

New edge types on Meeting:
- `PRECEDED_BY {gap_days: int}` (Meeting → Meeting) — temporal chain. After
  each meeting is written, find the most recent prior meeting sharing ≥1 attendee
  and MERGE this edge. Gives the graph a temporal spine per person/group.
- `CAUSED_BY {confidence: float}` (Meeting → Meeting | Decision) — if the
  meeting summary mentions a specific previous decision or explicitly references
  a prior meeting (phrase matching + topic overlap), MERGE this causal link.

New property on Meeting:
- `relevance_weight: float` — starts at 1.0 on creation. Decayed nightly:
  `weight = max(0.1, weight * 0.95)` (5% per day floor of 0.1). Used by the
  memory retrieval layer to weight context assembly — recent meetings matter more.

New node type:
- `MemorySession {id, query_text, answer_text, nodes_accessed, created_at}` —
  every call to the memory retrieval API creates one. Edges:
  `ACCESSED` (MemorySession → Meeting | Person | Topic | Fact) for every node
  that contributed to the answer. Gives the system a record of what it has been
  asked and what parts of the graph it relied on.

**Functions in `episodic_memory.py`:**
1. `link_temporal_chain(meeting_id, meeting_date, attendee_emails)` — pure Cypher.
2. `detect_causality(meeting, meeting_id)` — LM Studio call only if `follow_up_needed`
   is True (already extracted by extractor.py). Asks "does this summary reference
   a prior decision or meeting? If so, describe it in one sentence." Then finds
   the matching Decision or Meeting node by text similarity and MERGEs CAUSED_BY.
3. `decay_relevance()` — nightly: `MATCH (m:Meeting) SET m.relevance_weight =
   CASE WHEN m.relevance_weight IS NULL THEN 1.0
   ELSE max(0.1, m.relevance_weight * 0.95) END`

### 4 — Procedural Memory (`procedural_memory.py`)

**What:** Recurring workflows modelled as step-graphs, both seeded and discovered.

New node types:
- `Procedure {id, name, description, match_pattern, is_inferred, occurrence_count,
  created_at, updated_at}` — a workflow template.
- `ProcedureStep {id, name, description, order, created_at}` — one step in a workflow.

New edge types:
- `FOLLOWS_PROCEDURE {confidence: float}` (Meeting → Procedure)
- `HAS_STEP` (Procedure → ProcedureStep)
- `NEXT_STEP {condition: str | null}` (ProcedureStep → ProcedureStep)

**Seeded known procedures (in `setup_memgraph.py`, created once on startup):**

| Procedure | Match criteria |
|---|---|
| `sprint_planning` | kind=meeting, topics ∩ {sprint, backlog, velocity, story points} ≠ ∅, ≥3 attendees |
| `client_review` | topics ∩ {client, demo, feedback, presentation} ≠ ∅, ≥2 distinct orgs in attendees |
| `one_on_one` | exactly 2 attendees |
| `incident_response` | topics ∩ {incident, outage, bug, hotfix, urgent, down} ≠ ∅ |
| `project_kickoff` | topics ∩ {kickoff, onboarding, new project, launch} ≠ ∅ |
| `retrospective` | topics ∩ {retro, retrospective, what went well, improvements} ≠ ∅ |

Each procedure has seeded ProcedureStep nodes connected via NEXT_STEP edges
with logical step descriptions (e.g. sprint_planning: agenda → review backlog →
estimate → commit → close).

**Two functions in `procedural_memory.py`:**
1. `match_to_procedure(meeting, meeting_id)` — checks meeting against each
   known procedure's `match_pattern` dict. If matched: MERGE FOLLOWS_PROCEDURE
   edge, increment `Procedure.occurrence_count`. One meeting can match multiple
   procedures (sprint retro, for example). No LM call — pure pattern matching.
2. `discover_procedures()` — nightly job. Groups meetings that: share a
   community_id (already computed by graph_algorithms), have ≥60% topic overlap,
   and have ≥5 occurrences. If the group doesn't match any existing Procedure,
   create a new `Procedure {is_inferred: True}` and link all matching meetings.
   Uses `node_similarity.jaccard` from graph_algorithms to measure topic overlap.

### 5 — Memory Retrieval (`memory_retrieval.py`)

**What:** Natural language questions answered using graph context + LM Studio.
This is the unified interface that makes all four layers above useful to agents
and humans querying via MCP or the API.

**Pipeline (3 steps):**
1. **Entity extraction** — short LM Studio call: "Extract any people names,
   topic keywords, and date references from this question." Returns structured
   JSON with `{people: [...], topics: [...], date_range: ...}`.
2. **Graph context assembly** — for each extracted entity, query the subgraph:
   Person nodes with their Fact/Preference/KNOWS neighbors, Topic nodes with
   meeting history and algorithm scores, relevant Meetings weighted by
   `relevance_weight`, matched Procedures. Cap at 20 nodes to avoid
   context overflow.
3. **LM Studio synthesis** — system prompt: "You are a meeting memory assistant.
   Answer the question using only the graph context below." Context includes
   algorithm-computed scores (PageRank, community) as plain text summaries.
   Returns a natural language answer.

After synthesis: create a MemorySession node, MERGE ACCESSED edges to every
node that contributed to the context.

**New API endpoints in `main.py`:**
- `POST /graph/memory/query` — `{question: str}` → `{answer, session_id, nodes_used}`
- `GET /graph/memory/person/{email}` — full memory profile: semantic facts,
  preferences, episodic chain (last 10 meetings + PRECEDED_BY chain), procedures
  this person appears in, algorithm scores
- `GET /graph/memory/sessions` — recent MemorySession nodes (last 20)

---

## Module Boundaries (extends existing CLAUDE.md rules)

- `graph_algorithms.py` — the ONLY place MAGE `CALL` procedures appear. Never
  inline MAGE calls in `memgraph_client.py`, `graph_builder.py`, or anywhere else.
- `semantic_memory.py`, `episodic_memory.py`, `procedural_memory.py`,
  `memory_retrieval.py` — each owns its node type(s) and edge type(s). No
  cross-module node writes (e.g. semantic_memory.py does not write MemorySession).
- All Cypher lives in `memgraph_client.py` OR the memory modules above — not in
  `main.py`, `graph_builder.py`, or anywhere else.
- All LM Studio calls use the existing `extractor._get_client()` pattern (same
  client, same base URL env var) — no new LLM client setup.
- APScheduler jobs: algorithm jobs and memory jobs registered in `main.py`
  lifespan, not scattered across modules.

---

## Schema Summary

### New node types
`Fact`, `Preference`, `Procedure`, `ProcedureStep`, `MemorySession`

### New edge types
`HAS_FACT`, `PREFERS`, `KNOWS`, `INTERESTED_IN`,
`PRECEDED_BY`, `CAUSED_BY`, `FOLLOWS_PROCEDURE`,
`HAS_STEP`, `NEXT_STEP`, `ACCESSED`

### New node properties on existing types
- `Person`: `pagerank_score`, `community_id`, `betweenness_centrality`,
  `degree_centrality`, `wcc_id`
- `Topic`: `pagerank_score`, `community_id`
- `Meeting`: `relevance_weight`

---

## Success Criteria

- After processing a batch of meetings, `Person.pagerank_score` and
  `community_id` are populated and queryable via MCP
- `GET /graph/insights/influential` returns ranked people and topics
- `GET /graph/insights/communities` returns detected groups with their members
- `POST /graph/memory/query` with "what did Alice discuss last week?" returns a
  coherent answer grounded in graph nodes, not hallucinated
- Sprint planning meetings match the `sprint_planning` procedure automatically
- After 5+ similar unmatched meetings, a new inferred Procedure node is created

---

## Implementation Phases

| Phase | Deliverable |
|---|---|
| 21 | MAGE algorithms — `graph_algorithms.py`, schema indexes, API endpoints, scheduler wiring |
| 22 | Semantic memory — `semantic_memory.py`, Fact/Preference/KNOWS/INTERESTED_IN |
| 23 | Episodic memory — `episodic_memory.py`, temporal chain, causality, decay, MemorySession |
| 24 | Procedural memory — seeded procedures in setup, `procedural_memory.py`, discovery |
| 25 | Memory retrieval — `memory_retrieval.py`, NL query API, person profile, session log, docs |
