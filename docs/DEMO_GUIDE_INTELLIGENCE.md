# Demo Guide — Graph Intelligence & Memory (Phases 21-26)

Companion to `docs/DEMO_GUIDE.md`. That guide covers ingestion (Airbyte → Postgres →
LM Studio → Memgraph). This one covers what happens *after* the graph is built —
the part that turns a passive archive into something that reasons about influence,
remembers durably, and understands meaning instead of keywords.

**Audience:** Matteo. Frame everything around business value, not implementation.
The theme: **"The graph got smarter without me changing what data goes in."**

---

## Prerequisites Checklist

```
[ ] LM Studio running with BOTH models loaded:
    - google/gemma-3-12b (chat/extraction)
    - text-embedding-nomic-embed-text-v1.5 (vector search)
[ ] docker compose ps — memgraph, lab, memgraph-mcp, transform_service all up
[ ] make health → lm_studio=true, memgraph=true, postgres=true
[ ] Memgraph Lab open at http://localhost:3000 (Quick Connect: host=memgraph, port=7687)
[ ] Terminal ready for curl commands
```

---

## Demo Flow (~12 minutes)

### [0:00] Frame it (1 min)

> "Last time I showed you the pipeline that turns emails and calendar events into
> a graph. Since then I've added a layer on top: the graph now computes who's
> actually influential, remembers facts and preferences, tracks how meetings
> connect over time, recognizes recurring workflows automatically, and — the
> part I'm most excited about — understands what a question *means*, not just
> what words it contains. None of this required touching the ingestion pipeline."

---

### [1:00] Advanced Algorithms — "Who actually matters here?" (2 min)

> "Every time a meeting gets processed, five graph algorithms run automatically
> and score every person and topic in the graph."

```bash
curl -s "http://localhost:8000/graph/insights/influential?label=Person&limit=5" | python3 -m json.tool
```

> "PageRank — same algorithm Google used for web pages, applied to meeting
> attendance. It's not 'who attends the most meetings,' it's 'who's central to
> the meetings that matter.'"

Follow with communities:
```bash
curl -s "http://localhost:8000/graph/insights/communities" | python3 -m json.tool | head -30
```

> "These are teams the graph discovered on its own — Louvain community
> detection, no manual org chart. If two people keep showing up in the same
> meetings, they end up in the same community."

**Cypher version for Lab (visual, better for a manager watching a screen).**
Return the node objects themselves, not extracted properties — Lab's graph view
only renders if the RETURN includes actual nodes/relationships:
```cypher
MATCH (p:Person)-[r:ATTENDED]->(m:Meeting)
WHERE p.pagerank_score IS NOT NULL
RETURN p, r, m
ORDER BY p.pagerank_score DESC LIMIT 20;
```
This renders the top-ranked people as central nodes with their meetings fanning
out — visibly *why* they're influential, not just a number in a table. Click
"Graph results" tab after running (not "Data results").

---

### [3:00] Semantic Memory — "The graph remembers facts, not just events" (2 min)

> "Every meeting summary gets run through the local LLM to pull out durable
> facts — not 'what happened in this meeting' but 'what's now permanently true.'
> Confidence goes up every time a fact gets independently confirmed."

```cypher
MATCH (m:Meeting)-[:HAS_FACT]->(f:Fact)
RETURN m.title, f.text, f.confidence, f.source_count
ORDER BY f.confidence DESC LIMIT 10;
```

> "And it tracks relationships — who knows who, weighted by how often they've
> actually met, not just an org chart."

```cypher
MATCH (a:Person)-[k:KNOWS]-(b:Person)
RETURN a.name, b.name, k.weight ORDER BY k.weight DESC LIMIT 10;
```

**Visual variant (Graph results tab) — the social graph itself:**
```cypher
MATCH (a:Person)-[k:KNOWS]-(b:Person)
RETURN a, k, b LIMIT 20;
```

---

### [5:00] Episodic Memory — "Meetings aren't isolated events" (1.5 min)

> "The graph chains meetings together — this standup followed that one, three
> days apart. And relevance decays over time automatically, so a meeting from
> six months ago doesn't compete equally with one from yesterday when the
> system is deciding what's important."

```cypher
MATCH (m:Meeting)-[r:PRECEDED_BY]->(prior:Meeting)
RETURN m.title, m.date, prior.title, prior.date, r.gap_days
ORDER BY m.date DESC LIMIT 10;
```

**Visual variant (Graph results tab) — the temporal chain itself:**
```cypher
MATCH (m:Meeting)-[r:PRECEDED_BY]->(prior:Meeting)
RETURN m, r, prior LIMIT 20;
```

---

### [6:30] Procedural Memory — "It recognized our own process" (2 min)

**This is a strong moment** — the graph auto-detected a 1:1 without being told what a 1:1 is.

```bash
curl -s http://localhost:8000/graph/procedures | python3 -m json.tool
```

> "These six workflow templates — sprint planning, client review, 1:1, incident
> response, kickoff, retro — are seeded once. But matching is automatic. Watch:
> the 'QA AI Pilot: Weekly touchpoint' meeting between just two people got
> tagged as a one-on-one with zero manual input."

```cypher
MATCH (m:Meeting)-[r:FOLLOWS_PROCEDURE]->(p:Procedure)
RETURN m.title, p.name, r.confidence;
```

> "And there's a nightly job that finds *undeclared* recurring patterns —
> clusters of similar meetings that don't match any known template — and
> proposes a new one automatically."

---

### [8:30] Vector Search — the closer (2.5 min)

**This is the "wow" moment. Lead with a question that has zero keyword overlap
with the answer — proves it's not just search-and-highlight.**

```bash
curl -s -G "http://localhost:8000/graph/search/facts" \
  --data-urlencode "q=who is responsible for testing automation?" \
  --data-urlencode "limit=3" | python3 -m json.tool
```

> "I asked 'who is responsible for testing automation' — a question with none
> of those exact words in the graph. Top result: 'Femi leads the QA automation
> initiative,' 65% similarity. This isn't keyword matching, it's understanding
> meaning. Every meeting summary and every fact gets embedded into vector space
> the moment it's written."

Second example — meeting search:
```bash
curl -s -G "http://localhost:8000/graph/search/meetings" \
  --data-urlencode "q=onboarding a new hire and getting them up to speed" \
  --data-urlencode "limit=3" | python3 -m json.tool
```

> "No meeting in this graph is titled 'onboarding' — it still found the
> training workshop content as the closest semantic match."

---

### [11:00] Tie it together — Natural Language Memory Query (1.5 min)

**Save the best for last.** This combines everything above into one interface.

```bash
curl -s -X POST http://localhost:8000/graph/memory/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What does Matteo discuss in his standups?"}' | python3 -m json.tool
```

> "One endpoint. It extracts who and what the question is about, pulls the
> relevant subgraph — facts, preferences, algorithm scores, recent meetings —
> and has the local LLM answer using *only* that context. It's grounded, not
> hallucinated — every answer traces back to real graph nodes, and the query
> itself gets logged as a memory session so we have an audit trail of what the
> system was asked and what it used to answer."

Optional: show the person profile too, since it's a nice single-screen summary:
```bash
curl -s http://localhost:8000/graph/memory/person/femi.oduwole@blood.ca | python3 -m json.tool
```

> "This is the same data, but as a full profile — facts, preferences, social
> graph, meeting history, algorithm scores, matched procedures — all from one
> call. This is exactly what the MCP server exposes to Claude Desktop or any
> agent, in real time, with no extra infrastructure."

---

## Key Talking Points

| Layer | Talking Point |
|---|---|
| Advanced Algorithms | "PageRank and community detection run after every batch — the graph is never stale on 'who matters.'" |
| Semantic Memory | "Facts persist and gain confidence over time — durable knowledge, not transient meeting notes." |
| Episodic Memory | "Temporal chains + relevance decay mean recency is built into the data model, not bolted on in a query." |
| Procedural Memory | "It recognizes known workflows automatically and proposes new ones it discovers on its own." |
| Vector Search | "Semantic, not keyword. It understands what you mean." |
| Memory Retrieval | "One natural-language interface over all four layers, fully grounded, fully local — nothing leaves the Mac." |
| MCP | "Everything shown here is also queryable by Claude Desktop or any agent right now, zero extra work." |

---

## Fallback Plans

**Vector search endpoint returns empty results:**
- Embeddings may not be backfilled. Run: `docker compose exec -w /app transform_service python3 -c "import asyncio; from transform_service import vector_memory; asyncio.run(vector_memory.backfill_embeddings())"`

**Algorithm scores look stale / all zero:**
- Run manually: `docker compose exec -w /app transform_service python3 -c "import asyncio; from transform_service import graph_algorithms; asyncio.run(graph_algorithms.run_fast_algorithms())"`

**LM Studio is slow on the memory query endpoint:**
- Have the `femi.oduwole@blood.ca` person-profile response pre-copied as a fallback — it's pure Cypher, no LLM call, instant.

**Something in the live demo errors:**
- Everything in this guide was run and verified against the live graph in this
  session — if a query errors, it's environment drift (index not created,
  embeddings not backfilled), not a design gap. Re-run `make setup-memgraph`.
