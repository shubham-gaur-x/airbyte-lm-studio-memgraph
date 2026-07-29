# CLAUDE.md — airbyte-lm-studio-memgraph

Read this entire file before writing any code. Re-read it at the start of every session.
This is the authoritative source of truth for this project.

---

## What This Project Is

A **fully local** meeting-memory pipeline. Everything runs on your MacBook M2 Pro (16GB).
No cloud services. No tunnels. No "is the service awake?" problem during demos.

This is v4 of the meeting-memory pipeline, evolved from:
- v1 — Python + Obsidian vault
- v2 — n8n + Confluence + Jira
- v3 — Airbyte Cloud + Render + Groq + Memgraph Cloud (`shubham-gaur-x/airbyte-meeting`, DO NOT TOUCH)

**v4 goals:**
1. Fully local — everything runs via `docker compose up`
2. LM Studio + Gemma3:12b for local LLM inference (OpenAI-compatible API)
3. Memgraph local (Docker) + Memgraph MCP server for agent/LLM graph access
4. Airbyte Cloud still used for ingestion (showcases Airbyte to their team), but writes to local Postgres
5. Jira consumed via agents (Airbyte Jira source → agent handler), not just written to
6. Timeline view on Memgraph graph (day/week/month filters)
7. ACID-compliant graph writes (batch Cypher in single transactions)

---

## Machine

MacBook Pro 14-inch 2023 · Apple M2 Pro · 16GB RAM · macOS Tahoe 26.5.1
Model: `gemma3:12b` at Q4_K_M quantization (~7-8GB VRAM) via LM Studio

---

## Full Architecture (Local)

```
┌─────────────────────────────────────────────────────┐
│  SOURCES                                            │
│  Gmail · Google Calendar · Jira                     │
└──────────────────┬──────────────────────────────────┘
                   │ OAuth2 / API token
                   ▼
┌─────────────────────────────────────────────────────┐
│  AIRBYTE CLOUD                                      │
│  3 connectors · incremental sync · Append+Dedup     │
│  Destination: LOCAL Postgres (via ngrok tunnel)     │
│  Webhook on sync complete → transform service       │
└──────────────────┬──────────────────────────────────┘
                   │ normalized tables
                   ▼
┌─────────────────────────────────────────────────────┐
│  LOCAL POSTGRES (Docker)                            │
│  raw_emails · raw_calendar_events · raw_jira_issues │
│  processed_flag for exactly-once semantics          │
└──────────────────┬──────────────────────────────────┘
                   │ APScheduler polls every 5 min
                   │ + webhook on Airbyte sync
                   ▼
┌─────────────────────────────────────────────────────┐
│  TRANSFORM SERVICE (Python · FastAPI · Docker)      │
│                                                     │
│  classifier.py     rules-based meeting scorer       │
│  extractor.py      LM Studio gemma3:12b (local)     │
│  graph_builder.py  MERGE → local Memgraph (ACID)    │
│  jira_pusher.py    ActionItems → Jira sprint        │
│  jira_agent.py     Jira issues → consumed by agent  │
│  digest.py         weekly graph summary             │
└──────────────────┬──────────────────────────────────┘
                   │ Bolt protocol (localhost:7687)
                   ▼
┌─────────────────────────────────────────────────────┐
│  LOCAL MEMGRAPH (Docker)                            │
│  6 node types · 7 edge types · UNIQUE indexes       │
│  ACID transactions on all graph writes              │
│                                                     │
│  MEMGRAPH MCP SERVER (Docker sidecar)               │
│  run_query · get_schema · MCP_READ_ONLY=false       │
│  Connects: Claude Desktop / Claude Code / agents    │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│  FASTAPI QUERY LAYER (same service)                 │
│  /health                                            │
│  /graph/meetings/recent                             │
│  /graph/person/{email}                              │
│  /graph/topic/{name}                                │
│  /graph/actions/open                                │
│  /graph/timeline?window=day|week|month  ← NEW       │
│  /graph/digest/weekly                               │
│  /webhook/airbyte                                   │
└─────────────────────────────────────────────────────┘
```

---

## LM Studio Setup

LM Studio runs on the host Mac (not in Docker).
API endpoint: `http://host.docker.internal:1234/v1`
Model: `gemma3:12b-Q4_K_M`
Format: OpenAI-compatible (`/v1/chat/completions`)
No API key required. Temperature: 0.0. JSON mode via response_format.

In extractor.py, use openai SDK with:
```python
client = openai.AsyncOpenAI(
    base_url="http://host.docker.internal:1234/v1",
    api_key="lm-studio"  # dummy, required by SDK but ignored
)
```

DO NOT use Ollama. For extraction, DO NOT use Groq or any cloud LLM — extraction is always local. (Only the dev agent's coding backend is separately toggleable via `DEV_AGENT_LLM_BACKEND`; that never touches extractor.py.)

---

## Memgraph MCP Server

Runs as a Docker sidecar alongside local Memgraph.
Config:
- `MEMGRAPH_URL=bolt://memgraph:7687`
- `MCP_READ_ONLY=false` (write operations required for graph_builder)
- `MCP_TRANSPORT=streamable-http`
- Exposed on `localhost:8000/mcp/`

Claude Desktop config (user sets up once):
```json
{
  "mcpServers": {
    "memgraph": {
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

**IMPORTANT:** graph_builder.py does NOT use the MCP server for writes.
It uses the Memgraph Python driver directly (gqlalchemy or neo4j driver).
MCP server is for: Claude Desktop queries, agent read/write, demo exploration.

---

## ACID Compliance for Graph Writes

All multi-node writes in graph_builder.py MUST be wrapped in a single Cypher transaction.
Do NOT make sequential separate driver calls for related nodes/edges.

Pattern:
```python
async def build_graph(meeting, source_id):
    cypher = """
    MERGE (m:Meeting {id: $meeting_id}) SET m += $meeting_props
    MERGE (p:Person {email: $email}) SET p += $person_props
    MERGE (p)-[:ATTENDED {role: $role}]->(m)
    ...
    """
    await db.execute_transaction(cypher, params)
```

This ensures: if Jira push fails after graph write, the graph write already committed (by design).
If graph write partially fails, the whole transaction rolls back.

---

## Jira Agent (NEW in v4)

`jira_agent.py` — consumes Jira issues from `raw_jira_issues` table (written by Airbyte).

Flow:
1. Airbyte syncs Jira issues → local Postgres `raw_jira_issues`
2. Webhook triggers `process_jira_issues()`
3. Agent reads each issue, matches to Meeting/Person nodes in Memgraph
4. Updates ActionItem nodes with Jira status (Done/In Progress/etc.)
5. Closes the loop: graph reflects real Jira state

This is the bidirectional flow — not just writing TO Jira, but also reading FROM Jira via Airbyte.

---

## Autonomous Dev Agent (NEW in v4)

`dev_agent/` — a fully autonomous Jira ticket implementer that runs as a separate Docker service (`dev_agent`, port 8002).

**What it does:** Polls Jira every `DEV_AGENT_POLL_MINUTES` minutes and:
1. **Triage (BACKLOG → TO DO):** Promotes any ticket in BACKLOG that has a non-empty description and no `meeting-action-item` label. No human selects which tickets get worked — this gate is fully autonomous.
2. **Implement (TO DO → IN PROGRESS → IN REVIEW):** For each eligible TO DO ticket, transitions it to IN PROGRESS, creates a git worktree on branch `agent/<KEY>`, runs headless Claude Code against the ticket description, independently verifies a PR was opened, then transitions to IN REVIEW and posts the PR link as a Jira comment.

**Default backend is local LM Studio at zero cost — this is the demo path and the repo default.** Claude Code is pointed at LM Studio's native Anthropic-compatible endpoint (`LM_STUDIO_ANTHROPIC_URL`), and in local mode `ANTHROPIC_API_KEY` is explicitly emptied in the subprocess env so a key in the parent environment can never accidentally route traffic to api.anthropic.com. **A sanctioned, env-gated backend toggle (`DEV_AGENT_LLM_BACKEND`, default `local`) lets other users of this repo switch the dev agent's *coding* work to a hosted model** — real Anthropic Claude (`=claude`) or a free hosted tier (`=openrouter|gemini|groq`) — when they explicitly opt in and supply a key. The exception applies ONLY to dev-agent code implementation; meeting-data extraction stays local always. Local stays the default so the "fully local" demo story and privacy claim hold out of the box.

**The one remaining human checkpoint is merging the PR.** Auto-merge is explicitly NOT implemented in these phases. Do not add it without an explicit go-ahead.

**Failure path:** If Claude Code fails, times out, or produces no PR, the ticket returns to TO DO (not stuck in IN PROGRESS), a Jira comment says it needs human follow-up, and `dev_agent_runs` records `status=failed`. Tickets are not retried unless `DEV_AGENT_MAX_ATTEMPTS` is raised above 1.

**Lifecycle:** `BACKLOG` (no skip label + non-empty description) → `TO DO` (triage, autonomous) → `IN PROGRESS` (implementation starts, autonomous) → `IN REVIEW` (PR verified, autonomous) — or back to `TO DO` on failure.

See `docs/DEV_AGENT.md` for operational details and `docs/superpowers/specs/2026-06-29-autonomous-dev-agent-design.md` for the full design rationale.

---

## Timeline View (NEW in v4)

`GET /graph/timeline?window=day|week|month`

Queries Memgraph for nodes with `created_at` timestamps in the window.
Returns: meetings, decisions, action items grouped by date.
All nodes MUST have `created_at` (ISO datetime) property set on MERGE.

---

## Graph Schema

**Node types:** Meeting · Person · Organization · Topic · Decision · ActionItem
**Edge types:** ATTENDED · DISCUSSED · PRODUCED · ASSIGNED_TO · WORKS_AT · FOLLOWS_UP · MENTIONS

All nodes have: `id` (uuid5, deterministic) · `created_at` (ISO datetime) · `updated_at`
Meetings additionally have: `date` · `title` · `kind` · `platform` · `duration_minutes`

### Provenance layer (v5 / Phase 34 — dev-agent → graph)

**Node types:** Ticket · PullRequest · AgentRun (· Commit · FileChange from the GitHub webhook)
**Edge types:** TICKETED_AS (ActionItem→Ticket) · IMPLEMENTS (AgentRun→Ticket) · PRODUCED (AgentRun→PullRequest) · FOLLOWS_UP_ON (AgentRun→Meeting) · RESOLVED_BY (Ticket→PullRequest, on merge)

`AgentRun` is the "implementation session" bridge node (a dev-agent run). The edge
vocabulary is deliberately **aligned with Matteo's engagement ontology** (`~/Desktop/ontology`:
his DevLog `implements` a Feature and `follows_up_on` a Meeting) so our Memgraph graph is
legible to anyone who knows that ontology. One traversal returns
`meeting → action item → ticket → agent run → PR`.

Provenance nodes/edges are written ONLY via `memgraph_client.write_run_provenance`
(run outcome), `memgraph_client.merge_ticket_resolved_by_pr` (merge event), and
`memgraph_client.write_commits_and_files` (push). Node ids are re-derived to match
`dev_agent/lifecycle.py` exactly (run=uuid5("dev-agent-run", "KEY#attempt"),
ticket=uuid5("ticket", key), pr=uuid5("pullrequest", url)) — writer/reader id drift is a known
past bug class, so never derive these ids anywhere else.

- `transform_service/github_webhook.py` owns GitHub webhook PARSING only — it dispatches to
  `memgraph_client` and issues no Cypher and no GitHub REST itself. The `/webhook/github`
  receiver in `main.py` mirrors `/webhook/airbyte`. Join key = the `agent/<KEY>` branch.
- `dev_agent/self_verify.py` (P8) scores a PR diff against the ticket via `claude_runner.run_oneshot`
  through the SAME dev-agent backend. This is a sanctioned exception to "extraction is always
  local" (it scores CODE, not meeting data). It MUST NOT block the In Review transition — a low
  score only flags the Jira comment and sets `AgentRun.verified=false`.
- `dev_agent/github_client.py` owns all dev-agent GitHub REST (now `find_open_pr` + `get_pr_diff`).
- `dev_agent/session_memory.py` (P7) owns the resumable `AgentMemory` record (Matteo's shape),
  persisted in `dev_agent_runs.state_payload` (Postgres, keyed by ticket, survives attempts) — NOT
  the per-attempt `AgentRun` graph node, since the resume read happens before any PR/node exists.
- `Blocker` (P9) is a lightweight node written ONLY via `memgraph_client.merge_blocker`, created
  inline where first referenced (no extraction pipeline); edge `(Ticket)-[:RAISES_BLOCKER]->(Blocker)`
  mirrors Matteo's `raises_blocker`.
- `extractor.py` retry policy is explicit: transient API errors propagate and ARE retried by
  `@with_retry`; a JSON parse/validation failure returns None and is NOT retried (deterministic at
  temp 0) after a lenient first-`{...}` salvage. `jira_agent.sync_jira_issue` returns the real match
  result from `memgraph_client.update_action_jira_status` (which now returns a bool).
- `transform_service/person_resolver.py` (P3) owns entity resolution — email normalization + roster
  (deterministic) then fuzzy name match (probabilistic). It issues NO Cypher: `upsert_meeting_graph`
  calls it with `get_known_people()` and writes canonical `Person` nodes; unresolved attendees become
  `PersonReview` nodes `(Meeting)-[:NEEDS_REVIEW]->(:PersonReview)` — never silently dropped. `Person.tracked`
  (default false) is the opt-in gate: `get_influential_nodes` only ranks tracked people (governance —
  no per-person leaderboards by default). Roster comes from `PERSON_ROSTER_PATH` (JSON), empty if unset.
- `GET /graph/provenance/{meeting_id}` and `/graph/provenance/by-ticket/{ticket_key}` (B4) are the
  v5 target end-state query made real: one Cypher MATCH per direction returns
  meeting -> decision -> action item -> ticket -> AgentRun -> PR -> files. Row-grouping into the
  nested response shape is pure Python (`memgraph_client._group_meeting_provenance` /
  `_group_ticket_provenance`, unit-tested without a driver) — decisions are collected to one list
  before the row-multiplying action-item chain so they don't get duplicated per row.
- `GET /review/actions`, `/review/people`, `/review/blockers` (B3) surface the P4 needs_review
  ActionItems, P3 PersonReview nodes, and P9 Blocker nodes that were written but never read back
  anywhere — the read side of each gate now exists, in `memgraph_client.get_actions_needing_review` /
  `get_person_reviews` / `get_open_blockers`.
- `Decision.confidence` (B2 — the other P4 "dead field" gap) mirrors `ActionItem.confidence`: written by
  `upsert_meeting_graph`, defaulted to 1.0, coerced from a plain string via `ExtractedMeeting`'s
  `_coerce_decisions` validator for backward compatibility. `Fact.confidence` already had real dynamics
  (`semantic_memory`: seeded at 0.3, +0.1 per repeat mention) so its B2 gate is read-time, not write-time
  (a Fact has no Jira-ticket-style side effect to block): `memory_retrieval.person_memory_profile` floors
  Facts at `FACT_MIN_CONFIDENCE` (default 0.5).
- `transform_service/dedup.py` (P5) owns the pure dedup *decision* (embedding cosine, text-ratio
  fallback) — no I/O. `vector_memory` now also embeds `ActionItem` nodes
  (`embed_action_items_for_meeting`). `jira_pusher._find_duplicate` uses
  `memgraph_client.get_open_actions_for_owner` + `dedup.best_match`; on a match above
  `JIRA_DEDUP_THRESHOLD` it links `(existing ActionItem)-[:MENTIONED_IN]->(new Meeting)` and comments
  on the existing ticket instead of opening a duplicate (gated by `JIRA_DEDUP_ENABLED`, default true).
- `transform_service/meeting_type_router.py` (P6) is a cheap rules-based step between `classify()`
  (the "worth processing" gate) and `extract_meeting()`. `route()` picks a type (standup / planning /
  review / one_on_one / email_thread / general, derived from real meeting titles) and `prompt_hint()`
  returns type-specific guidance that `graph_builder` passes to `extract_meeting(type_hint=...)`, which
  appends it to the system prompt. Different types produce structurally different action items.
- `transform_service/transcript_source.py` (P1) is the swappable capture seam (`TranscriptSource`
  protocol; `DbTranscriptSource` reads `raw_meet_transcripts`). `graph_builder.process_transcript`
  treats the transcript text as the PRIMARY extraction input (calendar description is fallback only)
  and is otherwise identical to the email/event path. `transform_service/meet_ingest.py` is the live
  producer (Google Meet REST fetch + Cloud Pub/Sub PULL — no inbound tunnel; needs GCP creds,
  disabled no-op without them). Transcript rows are staged via `db.insert_meet_transcript` (SQL only
  in db.py); a different capture source (notetaker) implements the same seam without touching downstream.
- `process_new_transcripts` and `meet_ingest.pull_and_stage` (via `main._poll_meet_transcripts`, B5) now
  run on the same 5-minute scheduler interval as the other polls — previously only fired from
  `/webhook/airbyte`'s background_tasks, so a transcript staged directly by the Pub/Sub-pull consumer
  had nothing draining it until an unrelated Airbyte sync happened to run.

---

## Coding Conventions

- Python 3.11+ with type hints on ALL function signatures
- Pydantic v2 — `model_config = ConfigDict(extra="ignore")`
- `with_retry(max_attempts=3, base_delay=2.0)` on all external calls
- Structured logging with `structlog` — every log includes `source`, `meeting_id`, `step`
- `httpx.AsyncClient` for ALL HTTP calls — never `requests`
- No Cypher outside `memgraph_client.py`
- No SQL outside `db.py`
- All Cypher node/edge writes use `MERGE` not `CREATE`
- `uuid5_id(namespace, value)` from utils.py for deterministic UUIDs
- All graph writes in single transactions (ACID)

---

## Graph Memory + Advanced Algorithms

- `graph_algorithms.py` is the ONLY place for MAGE `CALL` procedures.
  Never embed `CALL module.procedure()` in `memgraph_client.py`, `graph_builder.py`,
  `main.py`, or any memory module.
- `semantic_memory.py` owns `Fact` and `Preference` nodes and `HAS_FACT`, `PREFERS`,
  `KNOWS`, `INTERESTED_IN` edges.
- `episodic_memory.py` owns `PRECEDED_BY`, `CAUSED_BY`, `MemorySession` nodes,
  and the `relevance_weight` decay.
- `procedural_memory.py` owns `Procedure` and `ProcedureStep` nodes and
  `FOLLOWS_PROCEDURE`, `HAS_STEP`, `NEXT_STEP` edges.
- `memory_retrieval.py` is the only module that exposes `full_memory_query` and
  `person_memory_profile` — never call these from `graph_builder.py` or `main.py`
  directly (those are query-time, not ingestion-time).
- `vector_memory.py` owns the `embedding` property on `Meeting` and `Fact` nodes
  (same pattern as `graph_algorithms.py` writing algorithm scores onto nodes it
  doesn't otherwise own). It generates embeddings via LM Studio's
  `/v1/embeddings` endpoint (`LM_STUDIO_EMBEDDING_MODEL`) and calls
  `graph_algorithms.vector_search()` for nearest-neighbor lookups — it never
  issues `CALL vector_search.*` directly.
- All LM Studio calls in memory modules reuse `extractor._get_client()` —
  no new `AsyncOpenAI` instances.
- `action_agent.py` is the ONLY application module allowed to use the Airbyte
  Agent SDK (`airbyte-agent-sdk`). It is a sanctioned query-time consumer of
  `memory_retrieval` and must never be called from `graph_builder.py`. One-off
  setup/probe scripts under `scripts/` (`test_action_agent_sdk.py`,
  `create_agents_jira_connector.py`) may also import the SDK — they aren't
  part of the running service.

---

## Absolute Rules — Do NOT Violate

- DO NOT use Ollama (replaced by LM Studio)
- DO NOT route meeting-data extraction (`extractor.py` + memory modules) through Groq or any cloud LLM — extraction is ALWAYS local LM Studio, no exceptions. (The dev agent's *coding* backend is the one sanctioned, env-gated, opt-in exception — see the `DEV_AGENT_LLM_BACKEND` note below; it never touches extraction.)
- DO NOT use Render, Railway, or any cloud deployment
- DO NOT use Memgraph Cloud (use local Docker Memgraph)
- DO NOT use Neon Postgres (use local Docker Postgres)
- DO NOT touch the v3 repo `shubham-gaur-x/airbyte-meeting`
- DO NOT use `CREATE` in Cypher for unique nodes — always `MERGE`
- DO NOT make sequential separate driver calls for related nodes — batch in one transaction
- DO NOT use synchronous `requests` library — always `httpx.AsyncClient`
- DO NOT hardcode any secret or API key in source code
- DO NOT put Cypher outside `memgraph_client.py`
- DO NOT put SQL outside `db.py` (or `dev_agent/db.py` for the dev agent's own table)
- DO NOT let `dev_agent` reach a hosted LLM UNLESS `DEV_AGENT_LLM_BACKEND` is explicitly set to a hosted value. Default is `local` (LM Studio, `ANTHROPIC_API_KEY` emptied). `DEV_AGENT_LLM_BACKEND=claude` (or a free hosted tier) is a sanctioned opt-in for other users of this repo, applying ONLY to dev-agent code implementation — never to extraction/meeting data. In local mode, api.anthropic.com must remain unreachable (empty key).
- DO NOT auto-merge PRs opened by the dev agent — human review is the one remaining checkpoint; do not jump ahead of it
- DO NOT put Jira REST calls outside `jira_client.py` (applies to `dev_agent` and `transform_service` alike)
- DO NOT use the Airbyte Agent SDK outside `action_agent.py`
- DO NOT call `action_agent` functions from `graph_builder.py` (query-time only)
- DO NOT let the action agent set any Jira status other than In Review
- DO NOT let `dev_agent` default `is_engineering_task` to `True` when missing — fail safe toward NOT auto-implementing
- DO NOT add MAGE CALL procedures outside `graph_algorithms.py`
- DO NOT call `memory_retrieval` functions during ingestion (`graph_builder.py`)
- DO NOT write `MemorySession` nodes outside `episodic_memory.log_memory_session()`

---

## Environment Variables

```env
# LM Studio
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1
LM_STUDIO_MODEL=gemma3-12b  # exact model name as shown in LM Studio
LM_STUDIO_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5  # must also be loaded in LM Studio

# Local Postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=meeting_memory
POSTGRES_USER=meeting_user
POSTGRES_PASSWORD=

# Local Memgraph
MEMGRAPH_HOST=memgraph
MEMGRAPH_PORT=7687
MEMGRAPH_USER=
MEMGRAPH_PASSWORD=

# Jira
JIRA_ENABLED=true
JIRA_DOMAIN=shubhamgaur1.atlassian.net
JIRA_EMAIL=shubham.gaur@onixnet.com
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=SCRUM
JIRA_BOARD_ID=1
JIRA_ISSUE_TYPE=Task
JIRA_CONFIDENCE_THRESHOLD=0.6          # P4: ActionItems below this become needs_review, not a ticket
FACT_MIN_CONFIDENCE=0.5                # B2 (P4 extension): read-time floor in person_memory_profile
PERSON_ROSTER_PATH=                    # P3: JSON roster for entity resolution (empty = none)

# Airbyte webhook verification
AIRBYTE_WEBHOOK_SECRET=

# Google Meet transcript capture (P1 — needs GCP creds; disabled no-op without them)
GOOGLE_ACCESS_TOKEN=                   # OAuth token with Meet + Pub/Sub scopes
MEET_PUBSUB_SUBSCRIPTION=              # projects/<p>/subscriptions/<s> (PULL subscription)
JIRA_DEDUP_ENABLED=true                # P5: dedup recurring action items
JIRA_DEDUP_THRESHOLD=0.9               # P5: min similarity to treat as a duplicate

# Action Agent (Airbyte Agents SDK — app.airbyte.ai; separate product from
# the ELT API credentials above)
ACTION_AGENT_ENABLED=true
ACTION_AGENT_BATCH_SIZE=5
AIRBYTE_AGENTS_CLIENT_ID=
AIRBYTE_AGENTS_CLIENT_SECRET=
AIRBYTE_AGENTS_CONNECTOR_ID=

# Service
PORT=8000
LOG_LEVEL=INFO

# Dev Agent (autonomous Jira ticket implementer — see docs/DEV_AGENT.md)
GITHUB_TOKEN=
GITHUB_OWNER=shubham-gaur-x
GITHUB_REPO=airbyte-lm-studio-memgraph
LM_STUDIO_ANTHROPIC_URL=http://host.docker.internal:1234
# Coding-backend toggle. Default local (LM Studio, $0). Hosted values are an opt-in
# exception for other users and apply ONLY to dev-agent code work, never to extraction.
DEV_AGENT_LLM_BACKEND=local            # local | claude | openrouter | gemini | groq
DEV_AGENT_MIN_CONTEXT=32768            # preflight fails fast below this
ANTHROPIC_API_KEY=                     # required only when DEV_AGENT_LLM_BACKEND=claude
DEV_AGENT_CLAUDE_MODEL=                # optional, DEV_AGENT_LLM_BACKEND=claude: pin the
                                       # Anthropic model for cost control (e.g. claude-haiku-4-5).
                                       # Empty = Claude Code's own default model.
DEV_AGENT_CONFIDENCE_THRESHOLD=0.6     # P4: skip autonomous pickup of low-confidence ActionItems
DEV_AGENT_VERIFY_THRESHOLD=0.6         # P8 self-verify: min confidence to count as "addresses ticket"
DEV_AGENT_VERIFY_TIMEOUT_SECONDS=180   # P8 self-verify: one-shot scoring call timeout
GITHUB_WEBHOOK_SECRET=                 # P2 /webhook/github HMAC secret (unset = accept, dev only)
OPENROUTER_API_KEY=                    # optional, DEV_AGENT_LLM_BACKEND=openrouter
GEMINI_API_KEY=                        # optional, DEV_AGENT_LLM_BACKEND=gemini
GROQ_API_KEY=                          # optional, DEV_AGENT_LLM_BACKEND=groq
DEV_AGENT_LM_MODEL=qwen2.5-coder-7b-instruct   # local coder model, loaded in LM Studio at 32k ctx (gemma3-12b stays for extraction)
DEV_AGENT_BACKLOG_STATUS=Backlog       # OBSOLETE once triage is sprint-based (Phase 28)
DEV_AGENT_TODO_STATUS=To Do
DEV_AGENT_IN_PROGRESS_STATUS=In Progress
DEV_AGENT_REVIEW_STATUS=In Review
DEV_AGENT_SKIP_LABELS=meeting-action-item
DEV_AGENT_POLL_MINUTES=10
DEV_AGENT_BATCH_SIZE=5
DEV_AGENT_MAX_TURNS=40
DEV_AGENT_TIMEOUT_SECONDS=1800
DEV_AGENT_MAX_ATTEMPTS=1
DEV_AGENT_GIT_NAME=Meeting Memory Dev Agent
DEV_AGENT_GIT_EMAIL=dev-agent@local
```

---

## What Was Removed vs v3

| v3 Component | v4 Status | Reason |
|---|---|---|
| Render.com | ❌ Removed | Everything local |
| Groq API | ❌ Removed | LM Studio replaces it |
| Memgraph Cloud | ❌ Removed | Local Docker Memgraph |
| Neon Postgres | ❌ Removed | Local Docker Postgres |
| ngrok (Ollama tunnel) | ❌ Removed | LM Studio is local |
| Ollama | ❌ Removed | LM Studio replaces it |
| APScheduler | ✅ Kept | Polls local Postgres every 5 min |
| Airbyte Cloud | ✅ Kept | Still the ingestion backbone |
| Jira push | ✅ Kept | ActionItems → sprint |
| digest.py | ✅ Kept | Weekly graph summary |
| Slack connector | ❌ Removed | Not providing signal, simplify |
| Memgraph MCP | ✅ NEW | Agent/LLM query interface |
| Jira agent (consume) | ✅ NEW | Bidirectional Jira flow |
| Timeline view | ✅ NEW | day/week/month graph filter |

---

## Plugin Setup (run once in Claude Code)

```bash
# Install Superpowers (TDD, planning, subagent workflows)
/plugin install superpowers@claude-plugins-official

# Install forge-skills (architecture contracts, agent personas)
/plugin marketplace add aneja5/forge-skills
/plugin install forge-skills@forge-skills
```

These activate automatically. Superpowers triggers brainstorming before coding,
TDD during implementation, and code review between tasks.
forge-skills provides /architect, /plan, /build, /review, /ship commands.
