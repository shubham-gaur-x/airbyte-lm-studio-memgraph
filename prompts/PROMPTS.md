# Claude Code Prompts — airbyte-lm-studio-memgraph
# Run these in Claude Code, one phase at a time.
# ALWAYS have CLAUDE.md open in the repo root before starting any phase.
# After each phase: run `make test` before moving to the next.

---

## PHASE 0 — Repo scaffold & plugin setup

```
Read CLAUDE.md in full.

First, install the required plugins:
  /plugin install superpowers@claude-plugins-official
  /plugin marketplace add aneja5/forge-skills
  /plugin install forge-skills@forge-skills

Then scaffold the repo structure:

airbyte-lm-studio-memgraph/
├── transform_service/
│   ├── __init__.py
│   ├── main.py           (FastAPI app — stub only)
│   ├── classifier.py     (stub)
│   ├── extractor.py      (stub)
│   ├── graph_builder.py  (stub)
│   ├── memgraph_client.py (stub)
│   ├── jira_pusher.py    (stub)
│   ├── jira_agent.py     (stub — NEW in v4)
│   ├── digest.py         (stub)
│   ├── db.py             (stub)
│   ├── models.py         (Pydantic v2 models)
│   ├── utils.py          (with_retry, uuid5_id, structlog setup)
│   └── requirements.txt
├── docker-compose.yml
├── Makefile
├── .env.example
├── .gitignore
└── README.md

requirements.txt must include:
  fastapi uvicorn[standard] httpx pydantic>=2.0 structlog
  openai gqlalchemy apscheduler python-dotenv
  neo4j asyncpg psycopg2-binary

.gitignore must include: .env __pycache__ *.pyc .venv *.egg-info

Do not implement any logic yet — stubs only.
```

---

## PHASE 1 — Docker Compose (full local stack)

```
Read CLAUDE.md in full.

Implement docker-compose.yml with these services:

postgres:
  image: postgres:15
  environment:
    POSTGRES_DB: meeting_memory
    POSTGRES_USER: meeting_user
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  ports: 5432:5432
  volumes: postgres_data:/var/lib/postgresql/data
  healthcheck: pg_isready -U meeting_user

memgraph:
  image: memgraph/memgraph-platform:latest
  ports: 7687:7687, 7444:7444, 3000:3000
  volumes: memgraph_data:/var/lib/memgraph
  environment:
    MEMGRAPH_USER: ${MEMGRAPH_USER:-""}
    MEMGRAPH_PASSWORD: ${MEMGRAPH_PASSWORD:-""}
  command: ["--schema-info-enabled=True", "--log-level=WARNING"]
  healthcheck: uses mgconsole

memgraph-mcp:
  image: memgraph/mcp-memgraph:latest
  ports: 8001:8000
  environment:
    MEMGRAPH_URL: bolt://memgraph:7687
    MEMGRAPH_USER: ${MEMGRAPH_USER:-""}
    MEMGRAPH_PASSWORD: ${MEMGRAPH_PASSWORD:-""}
    MCP_READ_ONLY: "false"
    MCP_TRANSPORT: streamable-http
  depends_on: [memgraph]

transform_service:
  build: ./transform_service
  ports: 8000:8000
  env_file: .env
  depends_on: [postgres, memgraph]
  volumes: ./transform_service:/app  # hot reload
  extra_hosts:
    - "host.docker.internal:host-gateway"  # access LM Studio on Mac

volumes: postgres_data, memgraph_data

Add Makefile targets:
  up:       docker compose up -d
  down:     docker compose down
  logs:     docker compose logs -f transform_service
  shell:    docker compose exec transform_service bash
  psql:     docker compose exec postgres psql -U meeting_user -d meeting_memory
  cypher:   docker compose exec memgraph mgconsole
  test:     docker compose exec transform_service python -m pytest -v
  reset-db: docker compose down -v && docker compose up -d
```

---

## PHASE 2 — Data models & utilities

```
Read CLAUDE.md in full.

Implement transform_service/models.py with Pydantic v2 models:

Attendee:
  name: str
  email: str
  role: str = "attendee"

ActionItem:
  owner: str
  task: str
  due: Optional[date]
  done: bool = False
  priority: Literal["high", "medium", "low"] = "medium"
  jira_key: Optional[str] = None

ExtractedMeeting:
  title: str
  kind: Literal["meeting", "email_thread", "call", "standup", "review", "other"]
  platform: str
  date: date
  start_time: Optional[time]
  end_time: Optional[time]
  duration_minutes: Optional[int]
  location: Optional[str]
  attendees: List[Attendee]
  summary: str
  topics: List[str]
  decisions: List[str]
  action_items: List[ActionItem]
  key_quotes: List[str]
  links: List[str]
  sentiment: Literal["positive", "neutral", "negative", "mixed"]
  follow_up_needed: bool
  confidence: float  # 0.0-1.0

RawEmail:        id, source_id, subject, from_email, to_emails, body, received_at, processed
RawCalendarEvent: id, source_id, title, description, start_time, end_time, attendees_json, processed
RawJiraIssue:   id, source_id, key, summary, status, assignee, priority, created_at, updated_at, processed

AirbyteWebhookPayload:
  connection_id: str
  status: str
  job_id: Optional[str]

Implement transform_service/utils.py:
  - uuid5_id(namespace: str, value: str) -> str  (deterministic UUID)
  - configure_logging() -> structlog logger
  - with_retry(max_attempts=3, base_delay=2.0) decorator for async functions
    exponential backoff, logs each retry at WARNING level
  - priority_from_due(due: Optional[date]) -> str
    due <= 14 days → "high", <= 60 days → "medium", else "low"
```

---

## PHASE 3 — Database layer (local Postgres)

```
Read CLAUDE.md in full.

Implement transform_service/db.py using asyncpg.

Tables to create on startup:
  raw_emails (id, source_id UNIQUE, subject, from_email, to_emails TEXT[],
              body TEXT, received_at TIMESTAMPTZ, processed BOOLEAN DEFAULT FALSE,
              created_at TIMESTAMPTZ DEFAULT NOW())

  raw_calendar_events (id, source_id UNIQUE, title, description TEXT,
                       start_time TIMESTAMPTZ, end_time TIMESTAMPTZ,
                       attendees_json JSONB, processed BOOLEAN DEFAULT FALSE,
                       created_at TIMESTAMPTZ DEFAULT NOW())

  raw_jira_issues (id, source_id UNIQUE, key VARCHAR(50), summary TEXT,
                   status VARCHAR(50), assignee VARCHAR(255), priority VARCHAR(50),
                   jira_created_at TIMESTAMPTZ, jira_updated_at TIMESTAMPTZ,
                   processed BOOLEAN DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT NOW())

Functions needed:
  create_staging_tables() — idempotent, uses CREATE TABLE IF NOT EXISTS
  get_unprocessed_emails(limit=50) -> List[RawEmail]
  get_unprocessed_events(limit=50) -> List[RawCalendarEvent]
  get_unprocessed_jira_issues(limit=50) -> List[RawJiraIssue]
  mark_processed(table: str, record_id: str)
  get_pool() -> asyncpg pool (singleton, lazy init)

Use connection string from env: postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB}
All queries use parameterized statements. No SQL outside this file.
```

---

## PHASE 4 — Memgraph client (ACID graph writes)

```
Read CLAUDE.md in full. Pay special attention to the ACID section.

Implement transform_service/memgraph_client.py.

Use the neo4j Python driver (async) to connect to bolt://memgraph:7687.
All multi-node writes MUST execute as a single Cypher query, not sequential calls.

Functions needed:

create_indexes() — run on startup:
  CREATE CONSTRAINT ON (m:Meeting) ASSERT m.id IS UNIQUE
  CREATE CONSTRAINT ON (p:Person) ASSERT p.email IS UNIQUE
  CREATE CONSTRAINT ON (t:Topic) ASSERT t.name IS UNIQUE
  CREATE CONSTRAINT ON (d:Decision) ASSERT d.id IS UNIQUE
  CREATE CONSTRAINT ON (a:ActionItem) ASSERT a.id IS UNIQUE
  CREATE CONSTRAINT ON (o:Organization) ASSERT o.domain IS UNIQUE
  CREATE INDEX ON :Meeting(date)
  CREATE INDEX ON :Meeting(created_at)
  CREATE INDEX ON :ActionItem(created_at)
  CREATE INDEX ON :Decision(created_at)

upsert_meeting_graph(meeting: ExtractedMeeting, source_id: str) -> str:
  Single Cypher transaction that MERGEs:
    - Meeting node
    - Person nodes for each attendee
    - Organization nodes (from email domains)
    - Topic nodes
    - Decision nodes
    - ActionItem nodes
    - All edges: ATTENDED, WORKS_AT, DISCUSSED, PRODUCED, ASSIGNED_TO
  All nodes get created_at = datetime.utcnow().isoformat()
  Returns meeting_node_id

update_action_jira_key(action_id: str, jira_key: str)
  SET ActionItem.jira_key and ActionItem.jira_status = "Open"

update_action_jira_status(jira_key: str, status: str)
  Match ActionItem by jira_key, update status — used by jira_agent.py

get_timeline(window: Literal["day","week","month"]) -> dict:
  Query nodes with created_at in the time window.
  Returns: {meetings: [...], decisions: [...], action_items: [...]}
  Day = last 24h, Week = last 7 days, Month = last 30 days.

get_driver() -> AsyncDriver (singleton)
close_driver()
```

---

## PHASE 5 — Classifier

```
Read CLAUDE.md in full.

Port classifier.py from v3 (shubham-gaur-x/airbyte-meeting/transform_service/classifier.py).
Adapt only: remove any Ollama/Groq references (classifier is rules-based, no LLM needed).

classify(text: str, metadata: dict) -> float:
  Returns a score 0.0-1.0.
  Score >= 0.6 = meeting-related, proceed to extraction.
  Score < 0.6 = skip.

Scoring signals (port from v3):
  - Subject/title contains meeting keywords
  - Has attendees/calendar metadata
  - Contains action item patterns
  - Contains decision language
  - Duration/time references
  - Multiple participants

No changes to logic — just port and adapt imports.
```

---

## PHASE 6 — Extractor (LM Studio + Gemma3)

```
Read CLAUDE.md in full. The LM Studio section is critical.

Implement transform_service/extractor.py.

This replaces Groq (v3) with LM Studio (local).
LM Studio exposes an OpenAI-compatible API — use the openai SDK.

Client setup:
  import openai
  client = openai.AsyncOpenAI(
      base_url=os.environ["LM_STUDIO_BASE_URL"],  # http://host.docker.internal:1234/v1
      api_key="lm-studio"  # dummy, required by SDK
  )

async def extract_meeting(text: str, source_type: str) -> Optional[ExtractedMeeting]:
  1. Build system prompt: expert meeting analyst, output ONLY valid JSON
  2. Build user prompt with the text
  3. Call client.chat.completions.create() with:
     model = os.environ["LM_STUDIO_MODEL"]  # e.g. "gemma3-12b"
     temperature = 0.0
     response_format = {"type": "json_object"}
     max_tokens = 2000
  4. Parse JSON response into ExtractedMeeting
  5. If parse fails: log ERROR, return None

JSON schema to extract (same as v3 ExtractedMeeting shape):
  title, kind, platform, date, start_time, end_time, duration_minutes,
  location, attendees, summary, topics, decisions, action_items,
  key_quotes, links, sentiment, follow_up_needed, confidence

Wrap with @with_retry(max_attempts=3, base_delay=2.0).
Log: source_type, text length, extraction duration_ms, confidence score.

Fallback: if LM Studio is unreachable (connection error), log CRITICAL and raise.
Do NOT silently return None on connection errors — fail loud so the operator knows LM Studio is down.
```

---

## PHASE 7 — Graph builder

```
Read CLAUDE.md in full. Pay special attention to ACID section.

Implement transform_service/graph_builder.py.

Orchestrates the full pipeline for one source record.
Calls: classifier → extractor → memgraph_client → jira_pusher.

async def process_email(email: RawEmail) -> bool:
  1. Build text = f"{email.subject}\n\n{email.body}"
  2. score = classify(text, {"from": email.from_email})
  3. If score < 0.6: mark_processed, return False (skip)
  4. meeting = await extract_meeting(text, "email")
  5. If not meeting: mark_processed, return False
  6. node_id = await upsert_meeting_graph(meeting, email.source_id)
  7. await push_action_items(meeting.action_items, meeting, node_id)
  8. await db.mark_processed("raw_emails", email.id)
  9. return True

async def process_calendar_event(event: RawCalendarEvent) -> bool:
  Similar to above. Build text from title + description + attendees.

async def process_new_emails():
  emails = await db.get_unprocessed_emails(limit=50)
  results = await asyncio.gather(*[process_email(e) for e in emails])
  log processed/skipped counts

async def process_new_events():
  Similar for calendar events.

Wrap each individual process_email/event in try/except — log ERROR, continue on failure.
Never let one bad record stop the batch.
```

---

## PHASE 8 — Jira pusher (port from v3)

```
Read CLAUDE.md in full.

Port transform_service/jira_pusher.py from v3.
(Reference: shubham-gaur-x/airbyte-meeting/transform_service/jira_pusher.py)

No logic changes needed — just confirm it still:
  - Creates Jira issues for action_items
  - Routes high priority to active sprint, medium/low to backlog
  - Calls memgraph_client.update_action_jira_key() after creation
  - Respects JIRA_ENABLED env var
  - Uses httpx.AsyncClient with @with_retry
  - Returns List[str] of created Jira keys

Only adaptation needed: update import paths to match new repo structure.
```

---

## PHASE 9 — Jira agent (NEW — consume Jira from Airbyte)

```
Read CLAUDE.md in full. See "Jira Agent" section for the full flow.

Implement transform_service/jira_agent.py — NEW in v4.

This is the reverse direction: reads Jira issues from raw_jira_issues
(populated by Airbyte Jira source connector) and updates Memgraph.

async def process_jira_issues():
  issues = await db.get_unprocessed_jira_issues(limit=100)
  for issue in issues:
    await sync_jira_issue(issue)

async def sync_jira_issue(issue: RawJiraIssue):
  1. Check if ActionItem with jira_key = issue.key exists in Memgraph
  2. If yes: update ActionItem.jira_status = issue.status
             update ActionItem.updated_at = issue.jira_updated_at
  3. If issue.status in ["Done", "Closed"]: set ActionItem.done = True
  4. Mark raw_jira_issues row as processed
  5. Log: issue.key, issue.status, matched=True/False

This closes the loop: Airbyte reads Jira → Postgres → agent → Memgraph graph updated.
Wrap in try/except per issue. Log unmatched issues at DEBUG (normal for external issues).
```

---

## PHASE 10 — FastAPI service (main.py)

```
Read CLAUDE.md in full.

Implement transform_service/main.py — the FastAPI application.

Lifespan (startup):
  1. db.create_staging_tables()
  2. memgraph_client.create_indexes()
  3. Start APScheduler: every 5 minutes call process_new_emails() + process_new_events() + process_jira_issues()
  4. Log: LM Studio URL, Memgraph host, Jira enabled, MCP server URL

Middleware:
  CORSMiddleware (allow_origins=["*"])
  Request logging: method, path, status_code, duration_ms

Endpoints:

POST /webhook/airbyte
  Body: AirbyteWebhookPayload
  If status != "succeeded": return {"status": "ignored"}
  Add background tasks: process_new_emails(), process_new_events(), process_jira_issues()
  Return immediately: {"status": "queued", "connection_id": payload.connection_id}

GET /health
  Return: {"status": "ok", "lm_studio": ping_lm_studio(), "memgraph": ping_memgraph(), "postgres": ping_postgres()}

GET /graph/meetings/recent
  Query Memgraph for last 10 meetings, return as JSON

GET /graph/person/{email}
  Query Memgraph for person node + their meetings/actions

GET /graph/topic/{name}
  Query Memgraph for topic + meetings that discussed it

GET /graph/actions/open
  Query Memgraph for ActionItems where done=False

GET /graph/timeline
  Query param: window=day|week|month (default: week)
  Call memgraph_client.get_timeline(window)
  Return grouped results

GET /graph/digest/weekly
  Return structured weekly summary from Memgraph

All endpoints use structlog. All Memgraph queries go through memgraph_client.
```

---

## PHASE 11 — Setup scripts & smoke test

```
Read CLAUDE.md in full.

1. scripts/setup_memgraph.py
   Connects to local Memgraph and:
   - Creates all UNIQUE constraints
   - Creates indexes on Meeting.date, Meeting.created_at, ActionItem.created_at
   - Prints summary of what was created
   Run once before first use: make setup-memgraph

2. scripts/test_pipeline.py — end-to-end smoke test:
   - Starts LM Studio check (GET /v1/models) — fail clearly if LM Studio not running
   - Inserts a sample email into local Postgres
   - Calls process_new_emails()
   - Queries Memgraph: assert meeting node exists, >= 1 attendee, >= 1 topic
   - Queries /graph/timeline?window=day — assert meeting appears
   - Prints PASS or FAIL with details
   Run with: make smoke-test

3. scripts/backfill.py
   Process ALL unprocessed rows from local Postgres.
   Args: --source EMAIL|CALENDAR|JIRA|ALL --limit N --dry-run --since YYYY-MM-DD
   Shows tqdm progress bar.
   Prints summary table at end.

4. Add to Makefile:
   setup-memgraph: docker compose exec transform_service python scripts/setup_memgraph.py
   smoke-test:     docker compose exec transform_service python scripts/test_pipeline.py
   backfill:       docker compose exec transform_service python scripts/backfill.py --source ALL
```

---

## PHASE 12 — Airbyte Cloud reconfiguration guide

```
Read CLAUDE.md in full.

Create docs/AIRBYTE_SETUP.md:

Step-by-step guide to point existing Airbyte Cloud connectors at local Postgres:

1. Expose local Postgres via ngrok:
   ngrok tcp 5432 --url your-static-domain.tcp.ngrok.io
   (one-time: create static TCP address at dashboard.ngrok.com → Cloud Edge → TCP Addresses)

2. Update Airbyte Cloud destination:
   - Go to existing Postgres destination in workspace ae67dfe0
   - Change host to: your-static-domain.tcp.ngrok.io
   - Change port to: ngrok TCP port
   - Keep DB name, user, password same
   - Test connection

3. Verify connectors still sync:
   - Trigger manual sync on Gmail connector
   - Check local Postgres: SELECT COUNT(*) FROM raw_emails;
   - Trigger manual sync on Jira connector
   - Check: SELECT COUNT(*) FROM raw_jira_issues;

4. Update webhook URL:
   - Airbyte Cloud → Connections → Notifications → Webhook
   - New URL: http://localhost:8000/webhook/airbyte  (local only, for dev)
   - For demo: expose port 8000 via ngrok temporarily

Note: ngrok must be running when Airbyte syncs. Schedule syncs during demo prep.
```

---

## PHASE 13 — Demo guide

```
Read CLAUDE.md in full.

Create docs/DEMO_GUIDE.md for showing this to Matteo and the Airbyte team.

Include:
1. Prerequisites checklist:
   - LM Studio running with gemma3:12b loaded
   - ngrok TCP tunnel running (for Postgres)
   - docker compose up (all services green)
   - make health → all systems OK

2. Demo flow (10 min):
   a. Show Airbyte Cloud connectors + last sync times
   b. Trigger manual sync, watch webhook fire in logs
   c. Show transform service logs: classify → extract → graph write
   d. Open Memgraph Lab (localhost:3000) — show graph visualization
   e. Show /graph/timeline?window=week — meetings over time
   f. Show /graph/digest/weekly — structured summary
   g. Show Jira: action items auto-created in sprint
   h. Show Claude Desktop querying graph via MCP (natural language → Cypher)

3. Talking points per component:
   - Airbyte: connectors, incremental sync, webhook trigger
   - LM Studio: local inference, no data leaves Mac, Gemma3 quality
   - Memgraph: graph relationships vs flat storage, ACID writes
   - MCP: any AI agent can now query the meeting memory graph
   - Timeline view: see how topics/decisions evolved over time
   - Jira loop: write AND read, full bidirectional sync

4. Fallback plan if LM Studio is slow:
   - Keep a pre-extracted sample in sample_data/sample_extracted.json
   - scripts/test_pipeline.py can use it with --use-sample flag
```

---

## Notes for running Claude Code

- Run phases 0 → 13 in order. Each phase assumes previous is complete.
- After each phase: `make test` before proceeding.
- Phase 6 (extractor) requires LM Studio running with gemma3:12b loaded.
- Phase 4 (Memgraph) requires `make up` first (Docker services running).
- Phase 12 requires ngrok account (free tier is fine).
- Superpowers will trigger TDD automatically — let it. Don't skip tests.
- If a phase feels too big, use `/plan` from forge-skills to break it down further.
