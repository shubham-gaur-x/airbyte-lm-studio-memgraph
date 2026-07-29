# airbyte-lm-studio-memgraph — Project Briefing

*Consolidated from project memory, current as of 2026-07-08.*

## What This Is

v4 of a meeting-memory pipeline that evolved from:
- v1 — Python + Obsidian vault
- v2 — n8n + Confluence + Jira
- v3 — Airbyte Cloud + Render + Groq + Memgraph Cloud (repo: `shubham-gaur-x/airbyte-meeting`, READ-ONLY, never touched)

**v4 goal:** Everything runs on a MacBook M2 Pro 16GB via `docker compose up`. No cloud services at demo time except Airbyte Cloud (kept for showcase) and Jira (core feature).

**Why:** Demo reliability + privacy (no meeting data leaves the Mac) + cost (no cloud LLM API).

**Demo audience:** Built for Shubham Gaur (Onix) to hand to Matteo, who presents further up to senior leadership — so this briefing, and the accompanying decks, are meant to be self-contained enough that Shubham doesn't need to be in the room to explain them.

## Current State — Fully Built and Live-Verified Through Phase 27

Not a scaffold — a working system with **126/126 tests passing**, real ingested data (74 meetings, 43 topics, 7 people, 264 graph edges), and every major subsystem confirmed working end-to-end against live services at least once (not just mocked unit tests).

| Success criterion | Status |
|---|---|
| `docker compose up` starts all services | ✅ |
| Sample email → extracted meeting in Memgraph | ✅ |
| `make test` passes green | ✅ 126/126 |
| Claude Desktop queries the graph via MCP in natural language | ✅ |
| Graph computes influence/communities/decay/facts automatically, answers NL questions | ✅ |
| Action agent autonomously drafts + posts + transitions real Jira tickets via Airbyte Agents SDK, visible on Airbyte's own dashboard | ✅ |
| Dev agent (code-writing autonomous agent) | ⚠️ Built, blocked — see Known Issues |

**Key constraints:**
- M2 Pro 16GB — Gemma3:12b Q4_K_M is the max feasible chat model (~7-8GB VRAM); `text-embedding-nomic-embed-text-v1.5` (768-dim) also loaded for embeddings
- Must keep Airbyte Cloud (showcase to Airbyte team / Matteo) — now *also* showcasing Airbyte Agents (app.airbyte.ai), a second, separate Airbyte surface with its own credentials
- Must keep Jira (action items → sprint routing is core feature)

## Architecture

**Data flow:**
Gmail / Google Calendar / Jira → **Airbyte Cloud** (3 connectors, incremental sync, Append+Dedup) → **local Postgres** (Docker, `processed_flag` for exactly-once) → **transform_service** (FastAPI, APScheduler + webhook) → classify → extract (LM Studio) → MERGE into **local Memgraph** (ACID transactions) → fires the intelligence layer (algorithms + 4 memory modules) → Jira push → **Memgraph MCP server** → Claude Desktop / agents / API.

Jira is bidirectional two separate ways: (1) `jira_pusher.py` writes ActionItems to Jira, `jira_agent.py` reads status back via Airbyte-synced `raw_jira_issues`; (2) separately, the **Airbyte Agents SDK** (`action_agent.py`) directly reads/writes/transitions tickets as an autonomous agent — a different Airbyte product (app.airbyte.ai) from Airbyte Cloud ELT (cloud.airbyte.com), with its own credential pair (`AIRBYTE_AGENTS_CLIENT_ID`/`SECRET`/`ORGANIZATION_ID`/`CONNECTOR_ID` vs. the ELT pair's `AIRBYTE_CLIENT_ID`/`SECRET`).

Memgraph itself runs as separate `memgraph-mage` + `lab` images (3.11.0), migrated off the older bundled `memgraph-platform` (2.14.1).

**Core pipeline modules (`transform_service/`):**
- `main.py` — FastAPI app, APScheduler jobs, all HTTP endpoints
- `classifier.py` — rules-based meeting scorer (≥0.40 proceeds), no LLM
- `extractor.py` — LM Studio + Gemma3:12b via the openai SDK; `_get_client()` is the singleton every other module reuses
- `graph_builder.py` — orchestrates classify → extract → graph write → intelligence layer → Jira push, one try/except per record
- `memgraph_client.py` — the only file with generic Cypher; `db.py` — the only file with SQL; `jira_client.py` — the only file with Jira REST calls
- `jira_pusher.py` / `jira_agent.py` — write/read halves of the core Jira loop
- `utils.py` — `uuid5_id`, `with_retry`, `strip_json_fences` (local LLMs wrap JSON in ```` ```json ```` fences despite instructions not to — every parsing module strips these)

**Graph intelligence layer (Phases 21–26):**
- `graph_algorithms.py` — the only file with MAGE `CALL` procedures (PageRank, community detection via Louvain/Leiden, betweenness/degree centrality, weakly connected components). Fast path after every meeting, full/accurate path nightly.
- `semantic_memory.py` — Fact, Preference nodes; KNOWS, INTERESTED_IN edges. Confidence starts 0.3, +0.1 per reconfirm, +0.2 nightly consolidation.
- `episodic_memory.py` — PRECEDED_BY, CAUSED_BY, MemorySession nodes; relevance decays ×0.95/day (floor 0.1) nightly.
- `procedural_memory.py` — Procedure/ProcedureStep nodes (6 seeded + auto-inferred).
- `vector_memory.py` — owns `.embedding` on Meeting/Fact (768-dim), semantic search with zero keyword overlap, verified live.
- `memory_retrieval.py` — natural-language query API over the whole graph, query-time only.

**Autonomous agents:**
- `dev_agent/` (separate Docker service) — Jira ticket → git worktree → headless Claude Code (routed to LM Studio) → GitHub PR → In Review. **Built and unit-tested, but currently blocked live** (see Known Issues).
- `action_agent.py` (Phase 27) — the only file using `airbyte-agent-sdk`. Picks up non-engineering `meeting-action-item` tickets `dev_agent` intentionally skips, drafts a deliverable via LM Studio grounded in graph context, posts + transitions via the Airbyte Agents SDK in hosted mode (every tool call visible on Airbyte's own dashboard). **Live-verified end-to-end** against real ticket SCRUM-47.

**Graph schema — 11 node types, 17 edge types:**
Nodes: Meeting, Person, Organization, Topic, Decision, ActionItem, Fact, Preference, Procedure, ProcedureStep, MemorySession.
Edges: ATTENDED, DISCUSSED, PRODUCED, ASSIGNED_TO, WORKS_AT, FOLLOWS_UP, HAS_FACT, PREFERS, KNOWS{weight}, INTERESTED_IN{weight}, PRECEDED_BY{gap_days}, CAUSED_BY{confidence}, FOLLOWS_PROCEDURE{confidence}, HAS_STEP, NEXT_STEP, ACCESSED.
All writes MERGE (never CREATE), in single transactions.

**Coding rules:** Python 3.11+ typed; Pydantic v2; `@with_retry(max_attempts=3, base_delay=2.0)` on external calls; `httpx.AsyncClient` only; module-boundary rules as above; `uuid5_id()` for every deterministic id, always re-derived identically wherever a node is later referenced.

## Build History

**Phases 0–18:** Core v4 pipeline — scaffold, docker-compose, models, db, memgraph_client, classifier/extractor/graph_builder, jira_pusher/jira_agent, main.py, setup scripts, docs. DONE.

**Phases 21–26 (Graph Memory + Intelligence, completed 2026-07-01):** graph_algorithms.py, semantic/episodic/procedural memory, memory_retrieval.py, vector_memory.py. All DONE, live-verified.

**Phase 27 (Action Agent, completed 2026-07-08):** Built via subagent-driven-development, design spec + implementation plan, 6 tasks + final whole-branch review, merged to main. Live end-to-end run against SCRUM-47 succeeded after fixing two real bugs found only by live testing (SDK returns typed Pydantic models not dicts; a transition failure was silently miscounted as success). SDK version pinned afterward specifically because the bug class was unpinned-dependency shape drift.

**Total test count: 126/126 passing.**

## Known Issues

**Dev agent (`dev_agent/`) — built, not currently demo-able live:**
1. LM Studio's loaded context length (8192) is smaller than Claude Code's own tool-definition overhead — needs LM Studio reloaded with a larger context window (a local config change, not application code).
2. This Jira project's workflow has only `To Do / In Progress / In Review / Done` — no literal `Backlog` status — so the triage step's JQL can structurally never find anything to promote. Would need rewriting against sprint-membership instead of a status value.

Live end-to-end testing on both agents found real bugs unit tests missed, twice: ID-derivation mismatches between a node's writer and a later reader, and unverified assumptions about a third-party SDK's actual shape. When in doubt about whether something works, verify live rather than trusting the architecture.

## Deliverables

- `docs/demo_assets/Meeting_Memory_v4_Overview.pptx` — 27 slides, business case + full technical depth + live-evidence screenshots (real Jira ticket, real Airbyte Agents dashboard, real Memgraph algorithm results). Built to be presentable without Shubham in the room.
- `docs/demo_assets/Graph_Intelligence_Demo.pptx` — narrower deck, memory/algorithms-focused.
- `docs/demo_assets/architecture_v4.jpg` — current architecture diagram, reflecting what's actually running (the v3-era diagram had a speculative "Airbyte Agents" box that was never built that way; this one shows the real Phase 27 integration).
