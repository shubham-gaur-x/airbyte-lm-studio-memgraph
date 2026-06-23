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

DO NOT use Ollama. DO NOT use Groq. DO NOT use any cloud LLM.

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

## Absolute Rules — Do NOT Violate

- DO NOT use Ollama (replaced by LM Studio)
- DO NOT use Groq or any cloud LLM API
- DO NOT use Render, Railway, or any cloud deployment
- DO NOT use Memgraph Cloud (use local Docker Memgraph)
- DO NOT use Neon Postgres (use local Docker Postgres)
- DO NOT touch the v3 repo `shubham-gaur-x/airbyte-meeting`
- DO NOT use `CREATE` in Cypher for unique nodes — always `MERGE`
- DO NOT make sequential separate driver calls for related nodes — batch in one transaction
- DO NOT use synchronous `requests` library — always `httpx.AsyncClient`
- DO NOT hardcode any secret or API key in source code
- DO NOT put Cypher outside `memgraph_client.py`
- DO NOT put SQL outside `db.py`

---

## Environment Variables

```env
# LM Studio
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1
LM_STUDIO_MODEL=gemma3-12b  # exact model name as shown in LM Studio

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

# Airbyte webhook verification
AIRBYTE_WEBHOOK_SECRET=

# Service
PORT=8000
LOG_LEVEL=INFO
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
