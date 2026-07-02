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

# Autonomous Dev Agent — Phases 14-20

# Design doc: docs/superpowers/specs/2026-06-29-autonomous-dev-agent-design.md
# Read it in full before Phase 14. It has the rationale for every decision below —
# these prompts only have the "what," the design doc has the "why."
#
# This revision is FULLY autonomous: no human selects which BACKLOG tickets get
# worked, the agent triages BACKLOG -> TO DO itself. The one remaining human
# checkpoint is merging the PR — that is deliberately out of scope here.
#
# Prerequisites before Phase 17 can actually run (not needed to write the code):
#   - A GitHub personal access token with `repo` scope, in .env as GITHUB_TOKEN
#   - LM Studio's context length for the loaded model raised to at least 25K tokens
#     (LM Studio -> model settings -> Context Length). Claude Code is context-heavy;
#     LM Studio's default is too small.
#   - LM Studio version 0.4.1 or later (for the native Anthropic-compatible endpoint)

## PHASE 14 — Classify action items as engineering vs. process work

```
Read CLAUDE.md and docs/superpowers/specs/2026-06-29-autonomous-dev-agent-design.md
in full. This phase amends two already-complete files (models.py, extractor.py) —
make the smallest change that satisfies the spec below, don't restructure either file.

Edit transform_service/models.py:
  Add to ActionItem:
    is_engineering_task: bool = False
  Field meaning: True if completing this action item means writing or modifying
  code in this repo. False for anything else — scheduling, communication,
  documentation outside the repo, decisions that don't require a code change, etc.

Edit transform_service/extractor.py:
  Add "is_engineering_task": true|false to the action_items object in the JSON
  schema inside _SYSTEM_PROMPT. Add one line of guidance right after the
  schema's action_items description making the distinction concrete, e.g.:
  "is_engineering_task is true only if completing this requires writing or
  changing code — not for scheduling, communication, or non-technical follow-ups."

  In the action_items sanitization loop (where owner/task get defaulted if the
  LLM returns null), also default is_engineering_task to false if missing —
  fail safed toward NOT auto-implementing rather than silently defaulting to true.

Optionally (small, low-risk addition): in transform_service/memgraph_client.py,
add a.is_engineering_task to the ActionItem node's SET clause in
upsert_meeting_graph, sourced from action.is_engineering_task. Same pattern as
the other ActionItem fields already set there (priority, done, etc.) — gives
this a queryable signal in the graph too, not just in Jira.

Update any existing tests for ExtractedMeeting/ActionItem parsing and for
extractor.py's JSON parsing to cover the new field (default False when absent
from a parsed payload, correctly parsed when present).
```

---

## PHASE 15 — Jira client extraction + classification-aware labeling

```
Read CLAUDE.md and the design doc in full.

Create transform_service/jira_client.py:
  - Move jira_headers() and jira_base_url() out of jira_pusher.py into here
    (same logic, no behavior change — jira_pusher.py should import them from
    this module afterward, not redefine them).

  - _adf_to_text(node) -> str
    Recursively flattens an Atlassian Document Format node into plain text.
    Handle: text, paragraph, heading, listItem, bulletList/orderedList,
    codeBlock, hardBreak. Unknown node types: recurse into "content" if
    present, otherwise return "". Good enough for typical ticket descriptions,
    not a complete ADF renderer.

  - async def search_issues(jql: str, fields: list[str] | None, max_results=50) -> list[dict]
    GET {base}/search/jql with query params jql, fields (comma-joined), maxResults.
    IMPORTANT: use /rest/api/3/search/jql, NOT /rest/api/3/search — the latter
    returns 410 Gone, it was fully retired by Atlassian in late 2025.

  - async def list_eligible_tickets(project_key: str, statuses: list[str],
    skip_labels: list[str], require_description: bool = False) -> list[dict]
    Build JQL: project = {project_key} AND status in ({statuses}) AND
    labels not in ({each skip label}). If require_description: append
    AND description is not EMPTY. Append ORDER BY created ASC. Call search_issues.
    Return [{key, summary, status, labels}, ...].
    This single function backs BOTH the dev agent's BACKLOG triage (status=
    [Backlog], require_description=True) and its TO DO implementation watch
    (status=[To Do], require_description=False, since triage already enforced
    that bar before promoting) — do not write two separate query builders.

  - async def get_issue_detail(key: str) -> dict
    GET {base}/issue/{key}?fields=summary,description,status,labels,priority,assignee
    Return {key, summary, description (via _adf_to_text, stripped), status, labels, priority}.

  - async def add_comment(key: str, text: str) -> None
    POST {base}/issue/{key}/comment with a one-paragraph ADF body wrapping `text`.

  - async def get_transitions(key: str) -> list[dict]
    GET {base}/issue/{key}/transitions, return the "transitions" array.

  - async def transition_issue(key: str, target_status_name: str) -> bool
    Call get_transitions, find the entry whose to.name matches target_status_name
    (case-insensitive). If none found: log a WARNING with the available target
    names and return False — do not raise, this is recoverable (e.g. workflow
    doesn't allow the transition from wherever the ticket currently is).
    If found: POST {base}/issue/{key}/transitions with {"transition": {"id": ...}},
    log INFO, return True.

  All network functions wrapped with @with_retry(max_attempts=3, base_delay=2.0).
  URL-encode the issue key in path segments.

Edit transform_service/jira_pusher.py:
  - Import jira_headers, jira_base_url from transform_service.jira_client instead
    of defining them locally. Remove the local definitions.
  - Add MEETING_ACTION_ITEM_LABEL = "meeting-action-item" as a module constant.
  - In push_action_items, when building each issue's payload: if
    action.is_engineering_task is True, do NOT add the label. If False, set
    "labels": [MEETING_ACTION_ITEM_LABEL] in the fields dict.
  - No other behavior changes — issues still land wherever Jira's project
    workflow puts new issues by default (Backlog, for this Scrum board).

Write tests: _adf_to_text on representative ADF trees (plain paragraph, bullet
list, code block, nested); transition_issue's matching logic (mock
get_transitions); jira_pusher's label branching for is_engineering_task=True
vs False. Mock httpx, don't hit real Jira.
```

---

## PHASE 16 — Dev agent package scaffold

```
Read CLAUDE.md and the design doc in full.

Create the dev_agent/ package (sibling to transform_service/, not inside it):

dev_agent/
├── __init__.py
├── models.py
├── db.py
├── requirements.txt
└── Dockerfile

dev_agent/models.py — Pydantic v2 models:

  DevAgentRun:
    ticket_key: str
    status: Literal["queued","running","pr_opened","failed","skipped"]
    branch_name: Optional[str]
    pr_url: Optional[str]
    pr_number: Optional[int]
    error: Optional[str]
    attempt_count: int = 0
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

  JiraTicket: key, summary, description: str = "", status: str = "",
    labels: list[str] = [], priority: Optional[str]

  ClaudeRunResult: success: bool, returncode: int, result_text: str = "",
    num_turns: Optional[int], duration_ms: int = 0, timed_out: bool = False

dev_agent/db.py — its own asyncpg pool (same Postgres instance as transform_service,
  separate pool — do not import transform_service.db, this service owns its own
  table and connection lifecycle):

  get_pool() -> asyncpg.Pool (singleton, lazy init, same DSN-from-env pattern as
    transform_service/db.py)

  ensure_table() — CREATE TABLE IF NOT EXISTS dev_agent_runs:
    ticket_key TEXT PRIMARY KEY, status TEXT NOT NULL, branch_name TEXT,
    pr_url TEXT, pr_number INTEGER, error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

  get_run(ticket_key) -> Optional[DevAgentRun]

  should_attempt(ticket_key, max_attempts: int) -> bool
    No row -> True. status in (running, pr_opened) -> False.
    status == failed -> attempt_count < max_attempts. Else -> True.

  start_run(ticket_key, branch_name) — INSERT ... ON CONFLICT (ticket_key) DO UPDATE,
    set status='running', bump attempt_count, reset finished_at/error, set started_at=NOW().

  finish_run(ticket_key, status, pr_url=None, pr_number=None, error=None) —
    UPDATE dev_agent_runs SET ... WHERE ticket_key = $1, finished_at = NOW().

  list_recent_runs(limit=50) -> list[DevAgentRun]

dev_agent/requirements.txt:
  fastapi uvicorn[standard] httpx pydantic>=2.0 structlog apscheduler
  python-dotenv asyncpg

dev_agent/Dockerfile:
  Base: python:3.11-slim
  Install via apt: git, curl, ca-certificates
  Install Node.js (LTS) — needed for the Claude Code CLI
  npm install -g @anthropic-ai/claude-code
  Install the GitHub CLI (gh) — apt repo or direct binary, whichever is more
  reliable for a slim Debian base; pin a version rather than always-latest.
  pip install -r dev_agent/requirements.txt
  Copy dev_agent/ in
  CMD: uvicorn dev_agent.orchestrator:app --host 0.0.0.0 --port 8000

No logic beyond table setup yet — this phase is scaffold + db.py only.
Write tests for db.py's should_attempt() against the four cases above
(no row, running, pr_opened, failed-under-limit, failed-over-limit).
```

---

## PHASE 17 — Git worktrees + headless Claude Code runner

```
Read CLAUDE.md and the design doc in full. Pay close attention to the LM Studio
section — this is the one place in dev_agent/ where a wrong env var means the
agent silently talks to the wrong (or no) model.

Implement dev_agent/git_ops.py:

  GitError(RuntimeError) — raised on any non-zero git subprocess exit.

  _authed_remote_url(owner, repo, token) -> str
    https://x-access-token:{token}@github.com/{owner}/{repo}.git

  async def ensure_repo_cloned(repo_dir, owner, repo, token) -> None
    If repo_dir/.git exists: git fetch origin main. Else: git clone the authed
    remote URL into repo_dir.

  async def create_worktree(repo_dir, work_dir, branch_name) -> None
    git fetch origin main, then remove any stale worktree/branch at this path
    from a previous failed attempt (ignore errors), then
    git worktree add -b {branch_name} {work_dir} origin/main

  async def remove_worktree(repo_dir, work_dir, branch_name, ignore_errors=False) -> None
    git worktree remove --force {work_dir}, then git branch -D {branch_name}.
    Log a warning (not an exception) on failure unless ignore_errors=True.

  async def has_changes(work_dir) -> bool
    git status --porcelain, return whether output is non-empty.

  Run all git commands via asyncio.create_subprocess_exec (never shell=True),
  capture stdout/stderr, raise GitError with the captured stderr on non-zero exit.

Implement dev_agent/claude_runner.py:

  async def run_claude_code(work_dir: str, prompt: str, timeout_seconds: int,
    max_turns: int, model: Optional[str] = None) -> ClaudeRunResult

  Build the subprocess env as a COPY of os.environ with these overrides:
    ANTHROPIC_BASE_URL = env LM_STUDIO_ANTHROPIC_URL, default
      "http://host.docker.internal:1234"
      (LM Studio's native Anthropic-compatible endpoint — LM Studio 0.4.1+,
      this is NOT the same /v1 OpenAI-compatible path the extraction
      pipeline uses, it's a separate endpoint on the same LM Studio server)
    ANTHROPIC_AUTH_TOKEN = "lmstudio"  (dummy, LM Studio doesn't check it)
    ANTHROPIC_API_KEY = ""  (explicit empty — must never fall back to a real
      Anthropic key if one happens to be in the parent environment)

  Build the command:
    ["claude", "-p", prompt,
     "--allowedTools", "Read,Glob,Grep,Edit,Write,Bash",
     "--permission-mode", "acceptEdits",
     "--output-format", "json",
     "--max-turns", str(max_turns)]
    + ["--model", model] if model is given

  Deliberately do NOT pass --bare — we want CLAUDE.md, superpowers, and
  forge-skills to load for this run, that's the whole point of running this
  inside the project repo rather than a bare sandbox.

  Run via asyncio.create_subprocess_exec with cwd=work_dir, env=env,
  stdout/stderr=PIPE. Wrap proc.communicate() in
  asyncio.wait_for(..., timeout=timeout_seconds):
    - On asyncio.TimeoutError: proc.kill(), await proc.wait(), return
      ClaudeRunResult(success=False, returncode=-1, timed_out=True,
      result_text="timed out", duration_ms=...)
    - On non-zero returncode: return ClaudeRunResult(success=False,
      returncode=proc.returncode, result_text=stderr tail (last ~2000 chars))
    - On success: json.loads(stdout), pull "result" (text), "num_turns",
      "is_error". Return ClaudeRunResult(success=not is_error, ...).
      If stdout isn't valid JSON despite returncode==0, log a WARNING and
      fall back to returning the raw stdout tail as result_text rather than
      raising — a parse failure here should not crash the whole batch.

  Log at INFO: work_dir, model, max_turns at start; duration_ms, num_turns,
  is_error at finish. Log at ERROR on timeout or non-zero exit, including a
  stderr snippet.

Write tests for claude_runner.py covering: successful JSON result parsing,
non-zero exit, timeout (mock asyncio.wait_for to raise TimeoutError), and
malformed JSON stdout. Mock the subprocess — these tests must not actually
shell out to `claude` or require LM Studio running.
```

---

## PHASE 18 — GitHub verification + orchestrator (triage + implement)

```
Read CLAUDE.md and the design doc in full.

Implement dev_agent/github_client.py:

  _github_headers() -> dict
    Authorization: Bearer {GITHUB_TOKEN}, Accept: application/vnd.github+json,
    X-GitHub-Api-Version: 2022-11-28

  async def find_open_pr(owner: str, repo: str, branch: str) -> Optional[dict]
    GET https://api.github.com/repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open
    Return {number, html_url} for the first match, or None if the list is empty.
    @with_retry(max_attempts=3, base_delay=2.0). This is read-only — it exists
    specifically so we never have to trust Claude Code's own claim that it
    opened a PR; we check GitHub directly.

Implement dev_agent/orchestrator.py — triage, implementation, and the FastAPI app.

  build_prompt(ticket: dict) -> str
    Construct the prompt sent to Claude Code. Must include:
      - The ticket key and full description (verbatim, from get_issue_detail)
      - An instruction to read CLAUDE.md and follow this repo's conventions
      - An instruction to implement the ticket, then run the test suite
        (pytest / make test) and confirm it passes before finishing
      - An instruction to NOT modify .env, secrets, or anything outside the
        repo working directory
      - An instruction to NOT merge or attempt to merge any PR itself
      - Explicit git/gh steps: git add -A, git commit -m "<short summary
        referencing the ticket key>", git push -u origin <branch>, then
        gh pr create --title "[<TICKET-KEY>] <summary>" --body "<body
        referencing the ticket and summarizing the change>" --base main
        --head <branch>
      - An instruction to print the final PR URL on its own line prefixed
        with "PR_URL: " as the last thing it does (belt-and-suspenders —
        github_client.find_open_pr is still the authoritative check)

  async def triage_backlog() -> None
    candidates = await jira_client.list_eligible_tickets(JIRA_PROJECT_KEY,
      [DEV_AGENT_BACKLOG_STATUS], DEV_AGENT_SKIP_LABELS, require_description=True)
    For each: await jira_client.transition_issue(key, DEV_AGENT_TODO_STATUS).
    Log a batch summary (considered, promoted, skipped-no-transition-available).
    This function does the BACKLOG -> TO DO move autonomously — no human
    selection step exists anywhere in this flow.

  async def process_ticket(ticket: dict) -> None
    Bind a structlog logger with ticket_key for every line in this function.
    1. branch_name = f"agent/{ticket['key']}"
    2. await db.start_run(ticket['key'], branch_name)
    3. await jira_client.transition_issue(ticket['key'], DEV_AGENT_IN_PROGRESS_STATUS)
       (log a warning if it returns False, but proceed regardless — board
       visibility is nice-to-have, not a hard dependency for the run itself)
    4. detail = await jira_client.get_issue_detail(ticket['key'])
    5. work_dir = f"{WORK_ROOT}/{ticket['key']}"
    6. try: await git_ops.create_worktree(REPO_DIR, work_dir, branch_name)
    7. prompt = build_prompt(detail)
    8. result = await claude_runner.run_claude_code(work_dir, prompt,
       timeout_seconds=DEV_AGENT_TIMEOUT_SECONDS, max_turns=DEV_AGENT_MAX_TURNS,
       model=DEV_AGENT_LM_MODEL)
    9. If not result.success:
       await db.finish_run(key, "failed", error=result.result_text[:2000])
       await jira_client.add_comment(key, "Dev agent could not complete this
       ticket automatically (see dev_agent logs). Needs human follow-up.")
       await jira_client.transition_issue(key, DEV_AGENT_TODO_STATUS)
       # back to TO DO, not stuck showing IN PROGRESS with nothing happening —
       # also makes it visible for a retry if DEV_AGENT_MAX_ATTEMPTS allows one
       return (do not raise — this is an expected outcome, not a bug)
    10. pr = await github_client.find_open_pr(GITHUB_OWNER, GITHUB_REPO, branch_name)
    11. If pr is None: treat as failure exactly like step 9 (including the
        transition back to TO DO), with error = "claude_code reported success
        but no PR was found for this branch" (the case where the model
        hallucinated success)
    12. await jira_client.add_comment(key, f"Implemented automatically.
        PR: {pr['html_url']}")
    13. await jira_client.transition_issue(key, DEV_AGENT_REVIEW_STATUS)
        (log a warning if False, but this is not fatal to the run — the PR
        and the Jira comment are the source of truth even if the status
        field itself didn't move)
    14. await db.finish_run(key, "pr_opened", pr_url=pr['html_url'], pr_number=pr['number'])
    15. finally: await git_ops.remove_worktree(REPO_DIR, work_dir, branch_name,
        ignore_errors=True) — always clean up the worktree regardless of outcome

    Wrap the whole function body (after step 2) in try/except Exception:
    log ERROR with exc_info=True, db.finish_run(key, "failed", error=str(exc)),
    attempt the same transition-back-to-TO-DO as step 9 (best-effort, inside
    the except block, swallow any further error from it), and do NOT re-raise
    — one bad ticket must never stop the batch, matching the convention
    already established in transform_service/graph_builder.py.

  async def poll_and_process() -> None
    1. await git_ops.ensure_repo_cloned(REPO_DIR, GITHUB_OWNER, GITHUB_REPO, GITHUB_TOKEN)
    2. await triage_backlog()
    3. tickets = await jira_client.list_eligible_tickets(JIRA_PROJECT_KEY,
       [DEV_AGENT_TODO_STATUS], DEV_AGENT_SKIP_LABELS)
    4. For each ticket, skip if not await db.should_attempt(ticket['key'],
       DEV_AGENT_MAX_ATTEMPTS)
    5. Cap the number newly attempted this cycle at DEV_AGENT_BATCH_SIZE —
       a sudden flood of eligible tickets (e.g. someone bulk-creates a dozen)
       should not monopolize the single local model for hours straight; the
       rest get picked up on the next poll
    6. Process the remaining tickets SEQUENTIALLY, not concurrently — this is
       intentional, a 16GB Mac running one local model should not be asked to
       serve multiple simultaneous coding-agent sessions
    7. Log a batch summary: total considered, attempted, skipped

  FastAPI app (same lifespan/middleware conventions as transform_service/main.py):
    Lifespan: db.ensure_table(), start an APScheduler job calling
      poll_and_process() every DEV_AGENT_POLL_MINUTES minutes
    GET /health -> {"status": "ok"}
    POST /trigger/{ticket_key} -> fetch that one ticket via jira_client.get_issue_detail,
      call process_ticket on it directly (manual single-ticket test path, bypasses
      the TO DO/label filter and triage — useful for testing against a specific ticket)
    POST /triage -> call triage_backlog() directly, return what it promoted
      (manual test path for the triage step alone)
    GET /runs -> {"runs": [...]} via db.list_recent_runs()

Write tests for process_ticket's branching (success, claude_runner failure,
no-PR-found-after-claimed-success, exception during git/jira calls — check
in each failure case that the ticket is transitioned back toward TO DO) and
for triage_backlog's promotion logic, by mocking git_ops/claude_runner/
github_client/jira_client/db. These are the highest-value tests in this whole
feature since they encode every failure mode this design exists to handle.
```

---

## PHASE 19 — Wiring: docker-compose, Makefile, env, CLAUDE.md

```
Read CLAUDE.md and the design doc in full.

Add to docker-compose.yml a new service:

  dev_agent:
    build:
      context: .
      dockerfile: dev_agent/Dockerfile
    ports:
      - "8002:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./transform_service:/app/transform_service:ro   # reuse jira_client.py, utils.py
      - ./dev_agent:/app/dev_agent
      - dev_agent_repo:/work/repo                         # persistent clone + worktrees
    extra_hosts:
      - "host.docker.internal:host-gateway"

Add dev_agent_repo to the top-level volumes: block (alongside postgres_data, memgraph_data).

Add to Makefile:
  dev-agent-logs:
  	docker compose logs -f dev_agent

  dev-agent-trigger:
  	curl -s -X POST http://localhost:8002/trigger/$(TICKET) | python3 -m json.tool

  dev-agent-triage:
  	curl -s -X POST http://localhost:8002/triage | python3 -m json.tool

  dev-agent-runs:
  	curl -s http://localhost:8002/runs | python3 -m json.tool

Add to .env.example (and document each in CLAUDE.md's Environment Variables section):

  # Dev Agent (autonomous Jira ticket implementer — see docs/DEV_AGENT.md)
  GITHUB_TOKEN=
  GITHUB_OWNER=shubham-gaur-x
  GITHUB_REPO=airbyte-lm-studio-memgraph
  LM_STUDIO_ANTHROPIC_URL=http://host.docker.internal:1234
  DEV_AGENT_LM_MODEL=gemma3-12b
  DEV_AGENT_BACKLOG_STATUS=Backlog
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

Update CLAUDE.md:
  - Add a new top-level section "## Autonomous Dev Agent" (after the existing
    "Jira Agent" section) summarizing: what it does, that it runs entirely on
    LM Studio (no Anthropic API key, no exception to the no-cloud-LLM rule —
    state this explicitly so a future session doesn't "fix" it into using the
    real Anthropic API), the fully-autonomous BACKLOG -> TO DO -> IN PROGRESS
    -> IN REVIEW lifecycle with no human selection step, and that PR merge is
    the one remaining human checkpoint (explicitly not in scope yet — do not
    build auto-merge as part of these phases).
  - Add to "Absolute Rules — Do NOT Violate":
      DO NOT call api.anthropic.com from dev_agent — LM Studio only
      DO NOT auto-merge PRs opened by the dev agent (human review required,
      for now — this will change in a future phase, do not jump ahead of it)
      DO NOT put Jira REST calls outside jira_client.py (this now applies to
      dev_agent too, not just transform_service)
      DO NOT let dev_agent default is_engineering_task to true when missing —
      fail safe toward NOT auto-implementing
  - Add the new env vars to the Environment Variables section.

make test should still pass after this phase (it only adds a new service +
config, no logic changes beyond what Phases 14/15 already made).
```

---

## PHASE 20 — Docs + manual test path

```
Read CLAUDE.md and the design doc in full.

Create docs/DEV_AGENT.md, matching the style of docs/AIRBYTE_SETUP.md:

1. What it does (2-3 sentences, link to the design doc for the why) — emphasize
   this is the fully autonomous revision: no human picks which tickets get
   worked, the agent triages BACKLOG -> TO DO itself.
2. Prerequisites:
   - LM Studio 0.4.1+ running, with a model loaded and its context length
     raised to at least 25K tokens (LM Studio -> server/model settings ->
     Context Length — manual step, not something docker-compose can set)
   - GITHUB_TOKEN with repo scope in .env
   - GITHUB_OWNER / GITHUB_REPO matching this repo
3. How to test without waiting for the poll cycle:
     make dev-agent-triage              # promote eligible BACKLOG tickets now
     make dev-agent-trigger TICKET=SCRUM-123
     make dev-agent-runs
     make dev-agent-logs
4. Known limitation: Gemma-family models have documented issues getting stuck
   repeating the same tool call inside Claude Code. If a run hits --max-turns
   without producing a PR, repeatedly, on tickets that look implementable:
   load a different model in LM Studio and change DEV_AGENT_LM_MODEL in .env
   — no code or rebuild needed, just restart dev_agent.
5. The full autonomous lifecycle: BACKLOG (no label, has a description) ->
   TO DO (triage) -> IN PROGRESS (implementation starts) -> IN REVIEW (PR
   verified) — or back to TO DO with a Jira comment if the agent couldn't
   complete it. meeting-action-item-labeled tickets are never touched at any
   stage. Note explicitly: there is currently no human checkpoint before
   TO DO or before IN PROGRESS — the only human checkpoint left in the
   entire loop is merging the PR.
6. What happens on failure: ticket returns to TO DO, dev_agent_runs shows
   status=failed with an error message, a Jira comment says it needs human
   follow-up. Not retried automatically unless DEV_AGENT_MAX_ATTEMPTS is
   raised above 1.

No code in this phase — documentation only.
```

---

## Notes for running Claude Code

- Run phases 0 → 13 in order for the base v4 pipeline. Each phase assumes the
  previous is complete.
- Run phases 14 → 20 in order for the autonomous dev agent, only after 0-13
  are done. Phase 14 amends models.py/extractor.py, Phase 15 edits
  jira_pusher.py, and Phase 18's orchestrator imports
  transform_service.jira_client and transform_service.utils — strict order
  matters here more than it did in 0-13.
- After each phase: `make test` before proceeding.
- Phase 6 (extractor) requires LM Studio running with gemma3:12b loaded.
- Phase 4 (Memgraph) requires `make up` first (Docker services running).
- Phase 12 requires ngrok account (free tier is fine).
- Phase 16's Dockerfile and Phase 19's docker-compose changes require
  `docker compose build dev_agent` before `make dev-agent-trigger` will work.
- Phase 17/18 require the prerequisites listed at the top of the "Autonomous
  Dev Agent" section above (GITHUB_TOKEN, LM Studio context length raised,
  LM Studio 0.4.1+) to actually run end-to-end — but the code itself can be
  written and unit-tested (with mocks) without them.
- This revision removes the BACKLOG → TO DO human gate entirely (see the
  design doc's Safety Notes). Auto-merge is explicitly NOT part of these
  phases — if a future session is tempted to "finish the loop" by adding it,
  that needs its own explicit go-ahead, not an assumption.
- Superpowers will trigger TDD automatically — let it. Don't skip tests.
- If a phase feels too big, use `/plan` from forge-skills to break it down further.

---

# Graph Memory + Advanced Algorithms — Phases 21-25

# Design doc: docs/superpowers/specs/2026-06-30-graph-memory-algorithms-design.md
# Read it in full before Phase 21. Especially the Module Boundaries section —
# MAGE CALL procedures in the wrong file is the most likely mistake.
#
# Key constraint to verify before coding Phase 21:
#   docker compose exec memgraph mgconsole --execute "CALL mg.procedures() YIELD *
#   WHERE name STARTS WITH 'pagerank' RETURN name LIMIT 5;"
# If that returns results, MAGE is available and you can proceed.
# If it returns nothing, the image bundled is not memgraph-platform — stop and flag.

## PHASE 21 — MAGE algorithms + schema extension

```
Read CLAUDE.md and docs/superpowers/specs/2026-06-30-graph-memory-algorithms-design.md
in full before writing a single line.

Update scripts/setup_memgraph.py — add new constraints/indexes after the
existing ones (do not remove any existing constraints):

  New constraints:
    CREATE CONSTRAINT ON (f:Fact) ASSERT f.id IS UNIQUE
    CREATE CONSTRAINT ON (pref:Preference) ASSERT pref.id IS UNIQUE
    CREATE CONSTRAINT ON (proc:Procedure) ASSERT proc.id IS UNIQUE
    CREATE CONSTRAINT ON (ps:ProcedureStep) ASSERT ps.id IS UNIQUE
    CREATE CONSTRAINT ON (ms:MemorySession) ASSERT ms.id IS UNIQUE

  New indexes:
    CREATE INDEX ON :Person(community_id)
    CREATE INDEX ON :Person(pagerank_score)
    CREATE INDEX ON :Topic(community_id)
    CREATE INDEX ON :Meeting(relevance_weight)
    CREATE INDEX ON :Fact(confidence)
    CREATE INDEX ON :MemorySession(created_at)

Create transform_service/graph_algorithms.py:

  This module is the ONLY place in the entire codebase where MAGE CALL
  procedures appear. Never add MAGE CALL procedures elsewhere.

  async def run_fast_algorithms() -> dict:
    """Event-driven path — called after each processed batch.
    Uses Louvain community_detection (faster) for speed."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      # Run each algorithm and write results back as node properties in the
      # same Cypher statement. Use separate session.run() calls per algorithm
      # so one failure doesn't abort the others — catch and log each individually.

      await session.run("""
          CALL pagerank.get()
          YIELD node, rank
          SET node.pagerank_score = rank
      """)

      await session.run("""
          CALL community_detection.get()
          YIELD node, community_id
          SET node.community_id = community_id
      """)

      await session.run("""
          CALL betweenness_centrality.get()
          YIELD node, betweenness_centrality
          SET node.betweenness_centrality = betweenness_centrality
      """)

      await session.run("""
          CALL degree_centrality.get()
          YIELD node, degree_centrality
          SET node.degree_centrality = degree_centrality
      """)

      await session.run("""
          CALL weakly_connected_components.get()
          YIELD node, component_id
          SET node.wcc_id = component_id
      """)

    log.info("graph_algorithms.fast_run_complete")
    return {"algorithms_run": ["pagerank", "community_detection",
            "betweenness_centrality", "degree_centrality", "wcc"]}

  async def run_full_algorithms() -> dict:
    """Nightly path — uses leiden_community_detection (more accurate)
    and recomputes all five algorithms over the full graph."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      # Same as run_fast_algorithms() but replace community_detection with
      # leiden_community_detection. Catch and log failures individually.
      # leiden call:
      #   CALL leiden_community_detection.get()
      #   YIELD node, community_id
      #   SET node.community_id = community_id
      # Run all five. Log a summary dict of how many nodes were updated
      # (use a COUNT query after each SET).
      pass  # implement fully — this is not pseudocode

    log.info("graph_algorithms.full_run_complete")
    return {"algorithms_run": ["pagerank", "leiden_community_detection",
            "betweenness_centrality", "degree_centrality", "wcc"]}

  async def get_jaccard_similarity(node_id_a: str, node_id_b: str) -> float:
    """On-demand: Jaccard similarity between two nodes based on shared
    neighbors. Used by procedural memory's discover_procedures()."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      result = await session.run("""
          MATCH (a {id: $id_a}), (b {id: $id_b})
          CALL node_similarity.jaccard(a, b)
          YIELD similarity
          RETURN similarity
      """, id_a=node_id_a, id_b=node_id_b)
      record = await result.single()
      return record["similarity"] if record else 0.0

Add to transform_service/memgraph_client.py — new query functions only,
no MAGE CALL procedures here:

  async def get_influential_nodes(label: str = "Person", limit: int = 10) -> list:
    """Return top N nodes by pagerank_score for a given label."""
    ...MATCH (n:{label}) WHERE n.pagerank_score IS NOT NULL
    RETURN n.id, n.name OR n.email OR n.name AS name,
    n.pagerank_score, n.community_id ORDER BY n.pagerank_score DESC LIMIT limit...

  async def get_community_members(community_id: int) -> list:
    """Return all nodes in a given community_id."""

  async def get_bridge_nodes(limit: int = 10) -> list:
    """Return top N nodes by betweenness_centrality."""

  async def get_node_insights(node_id: str) -> dict:
    """Return algorithm scores for a specific node."""

Update transform_service/graph_builder.py:
  After the existing await push_action_items(...) line in BOTH process_email
  and process_calendar_event, add:
    from transform_service import graph_algorithms
    try:
        await graph_algorithms.run_fast_algorithms()
    except Exception as exc:
        log.warning("graph_builder.algorithms_skipped", error=str(exc))
  This must be inside the try block, after push_action_items, before
  mark_processed. It must NOT raise — algorithm failure should never
  prevent a meeting from being marked processed.

Update transform_service/main.py:
  In the lifespan function, add two new scheduler jobs after the existing ones:
    scheduler.add_job(
        graph_algorithms.run_full_algorithms,
        "cron", hour=2, minute=0,
        id="nightly_algorithms"
    )
  Import graph_algorithms at the top of main.py.

Add four new GET endpoints to main.py (no request body, just path/query params):
  GET /graph/insights/influential?label=Person&limit=10
      → calls memgraph_client.get_influential_nodes(label, limit)
  GET /graph/insights/communities
      → MATCH (n) WHERE n.community_id IS NOT NULL
        RETURN n.community_id, collect(n.id) as members grouped by community_id
  GET /graph/insights/bridges?limit=10
      → calls memgraph_client.get_bridge_nodes(limit)
  GET /graph/insights/node/{node_id}
      → calls memgraph_client.get_node_insights(node_id)

Write tests: mock the Memgraph driver and assert each algorithm function
runs the correct CALL statement and handles individual algorithm failure
gracefully (one algorithm failing should not abort the others).
```

---

## PHASE 22 — Semantic memory

```
Read CLAUDE.md and the design doc in full.

Create transform_service/semantic_memory.py:

  Import: memgraph_client (for driver), extractor._get_client() for LM Studio
  (do NOT create a new LLM client — reuse the existing singleton), utils.uuid5_id.

  async def extract_facts(meeting: ExtractedMeeting, meeting_id: str) -> int:
    """Call LM Studio to extract 3-5 durable facts from the meeting summary.
    MERGE Fact nodes. Return count of facts created."""

    Prompt (short, temperature=0.0):
      System: "Extract 3-5 durable facts from this meeting summary.
      A fact is something persistently true about a person, project, or topic —
      not a one-time event. Respond ONLY with a JSON array of strings.
      Example: [\"Alice leads the backend team\", \"The API migration is Q3\"]"
      User: meeting.summary

    Parse the JSON array. For each fact_text:
      fact_id = uuid5_id("fact", fact_text.lower().strip())
      MERGE (f:Fact {id: $fact_id})
      ON CREATE SET f.text = $text, f.confidence = 0.3, f.source_count = 1,
                    f.created_at = $now
      ON MATCH SET f.source_count = f.source_count + 1,
                   f.confidence = min(1.0, f.confidence + 0.1),
                   f.updated_at = $now
      WITH f
      MATCH (m:Meeting {id: $meeting_id})
      MERGE (m)-[:HAS_FACT]->(f)

    Return count of facts written.

  async def infer_preferences(meeting: ExtractedMeeting, meeting_id: str) -> int:
    """Infer preferences for attendees with >= 3 meetings in the graph.
    Skip attendees with fewer meetings — not enough signal yet."""

    First query: for each attendee email in meeting.attendees, check
    MATCH (p:Person {email: $email})-[:ATTENDED]->(m:Meeting)
    RETURN count(m) as meeting_count. Only process those with meeting_count >= 3.

    For each eligible attendee, one LM Studio call:
      System: "Given this person's meeting history context, infer 1-2 preferences
      about how they work. Respond ONLY with a JSON array of objects:
      [{\"category\": \"string\", \"value\": \"string\"}].
      Categories: communication_style, meeting_frequency, topic_interest,
      work_pattern, timezone_preference. Be specific, not generic."
      User: f"Person: {attendee.name}\nRole: {attendee.role}\n
             Meeting topics: {', '.join(meeting.topics)}\n
             Meeting kind: {meeting.kind}"

    For each preference object:
      pref_id = uuid5_id("preference", f"{attendee.email}:{pref['category']}:{pref['value']}")
      MERGE (pref:Preference {id: $pref_id})
      ON CREATE SET ...
      MERGE (p:Person {email: $email})-[:PREFERS]->(pref)

    Return count of preferences written.

  async def strengthen_relationships(meeting: ExtractedMeeting, meeting_id: str) -> None:
    """Pure Cypher — no LM call. Strengthen KNOWS and INTERESTED_IN edges."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      emails = [a.email for a in meeting.attendees if a.email]

      # KNOWS: for each pair of attendees who both attended this meeting
      # MERGE the relationship and increment weight by 1.
      # Use UNWIND on the pair list approach — see below.
      await session.run("""
          UNWIND $emails AS email1
          UNWIND $emails AS email2
          WITH email1, email2 WHERE email1 < email2
          MATCH (p1:Person {email: email1}), (p2:Person {email: email2})
          MERGE (p1)-[k:KNOWS]->(p2)
          ON CREATE SET k.weight = 1, k.created_at = $now
          ON MATCH SET k.weight = k.weight + 1, k.updated_at = $now
      """, emails=emails, now=now)

      # INTERESTED_IN: for each attendee × each topic in this meeting
      for topic_name in meeting.topics:
          await session.run("""
              UNWIND $emails AS email
              MATCH (p:Person {email: email}), (t:Topic {name: $topic})
              MERGE (p)-[i:INTERESTED_IN]->(t)
              ON CREATE SET i.weight = 1, i.created_at = $now
              ON MATCH SET i.weight = i.weight + 1, i.updated_at = $now
          """, emails=emails, topic=topic_name, now=now)

  async def consolidate_semantic() -> dict:
    """Nightly: raise confidence of well-confirmed facts.
    Facts with source_count in [3,6,9,...] get a confidence boost."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      result = await session.run("""
          MATCH (f:Fact)
          WHERE f.source_count % 3 = 0 AND f.confidence < 1.0
          SET f.confidence = min(1.0, f.confidence + 0.2)
          RETURN count(f) AS boosted
      """)
      record = await result.single()
      return {"facts_boosted": record["boosted"] if record else 0}

Wire into transform_service/graph_builder.py:
  After the existing call to graph_algorithms.run_fast_algorithms() (added
  in Phase 21), add — inside the same try/except wrapper:
    from transform_service import semantic_memory
    await semantic_memory.extract_facts(meeting, node_id)
    await semantic_memory.infer_preferences(meeting, node_id)
    await semantic_memory.strengthen_relationships(meeting, node_id)

Add to nightly APScheduler job in main.py:
  scheduler.add_job(semantic_memory.consolidate_semantic, "cron",
                    hour=2, minute=15, id="nightly_consolidate_semantic")

Write tests: mock LM Studio client and driver. Test extract_facts JSON parsing
(valid array, invalid JSON falls back gracefully — returns 0 facts, does not
raise). Test strengthen_relationships Cypher is called with the correct UNWIND
pairs. Test consolidate_semantic query logic.
```

---

## PHASE 23 — Episodic memory

```
Read CLAUDE.md and the design doc in full.

Create transform_service/episodic_memory.py:

  async def link_temporal_chain(meeting_id: str, meeting_date: str,
                                  attendee_emails: list[str]) -> bool:
    """Find the most recent previous meeting sharing ≥1 attendee email
    and MERGE a PRECEDED_BY edge. Pure Cypher, no LM call.
    Returns True if a link was created."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      result = await session.run("""
          MATCH (current:Meeting {id: $meeting_id})
          MATCH (prev:Person)-[:ATTENDED]->(prior:Meeting)
          WHERE prev.email IN $emails
            AND prior.date < $meeting_date
            AND prior.id <> $meeting_id
          WITH prior ORDER BY prior.date DESC LIMIT 1
          MERGE (current)-[p:PRECEDED_BY]->(prior)
          ON CREATE SET p.gap_days = duration.inDays(
              date(prior.date), date($meeting_date)).days,
              p.created_at = $now
          RETURN prior.id AS prior_id
      """, meeting_id=meeting_id, emails=attendee_emails,
          meeting_date=meeting_date, now=now)
      record = await result.single()
      return record is not None

  async def detect_causality(meeting: ExtractedMeeting, meeting_id: str) -> int:
    """Only runs when meeting.follow_up_needed is True.
    Asks LM Studio whether this meeting references a prior decision or meeting.
    If yes, finds the match and MERGEs CAUSED_BY. Returns count of causal
    links created."""

    if not meeting.follow_up_needed:
        return 0

    prompt_system = """You are analyzing whether a meeting was caused by a prior
    decision or event. Given a meeting summary, identify if it explicitly continues
    or responds to a prior decision. Respond ONLY with JSON:
    {"references_prior": true/false, "reference_description": "one sentence or null"}"""
    prompt_user = meeting.summary

    # Call LM Studio (reuse extractor._get_client())
    # If references_prior is False: return 0
    # If True: search for a Decision node whose text has >50% word overlap
    # with reference_description. If found: MERGE (m)-[:CAUSED_BY {confidence: 0.7}]->(d)
    # If no Decision found, try Meeting nodes by title similarity.
    # Cap at 1 causal link per meeting to avoid noise.

    ...implement fully...
    return links_created

  async def decay_relevance() -> dict:
    """Nightly: decay Meeting.relevance_weight by 5% per day, floor 0.1."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      result = await session.run("""
          MATCH (m:Meeting)
          SET m.relevance_weight = CASE
              WHEN m.relevance_weight IS NULL THEN 1.0
              WHEN m.relevance_weight <= 0.1 THEN 0.1
              ELSE m.relevance_weight * 0.95
          END
          RETURN count(m) AS updated
      """)
      record = await result.single()
      return {"meetings_decayed": record["updated"] if record else 0}

  async def log_memory_session(query_text: str, answer_text: str,
                                node_ids: list[str]) -> str:
    """Create a MemorySession node and ACCESSED edges to every contributing node.
    Returns the session_id. Called by memory_retrieval.py, not graph_builder.py."""
    import hashlib
    session_id = uuid5_id("session", f"{now}{query_text}")
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
      await session.run("""
          MERGE (ms:MemorySession {id: $session_id})
          SET ms.query_text = $query_text,
              ms.answer_text = $answer_text,
              ms.nodes_accessed = $node_count,
              ms.created_at = $now
      """, session_id=session_id, query_text=query_text,
          answer_text=answer_text, node_count=len(node_ids), now=now)
      for node_id in node_ids:
          await session.run("""
              MATCH (ms:MemorySession {id: $session_id})
              MATCH (n {id: $node_id})
              MERGE (ms)-[:ACCESSED]->(n)
          """, session_id=session_id, node_id=node_id)
    return session_id

Wire into transform_service/graph_builder.py:
  After semantic_memory calls in process_email and process_calendar_event:
    from transform_service import episodic_memory
    emails = [a.email for a in meeting.attendees if a.email]
    await episodic_memory.link_temporal_chain(node_id, str(meeting.date), emails)
    await episodic_memory.detect_causality(meeting, node_id)
  Same try/except wrapper — failures must not prevent mark_processed.

  Additionally, on FIRST creation of a Meeting (before any algorithms),
  set relevance_weight = 1.0. Add this to the MERGE in memgraph_client.py:
    ON CREATE SET m.relevance_weight = 1.0

Add to nightly APScheduler in main.py:
  scheduler.add_job(episodic_memory.decay_relevance, "cron",
                    hour=2, minute=30, id="nightly_decay")

Add to main.py:
  GET /graph/memory/sessions (returns last 20 MemorySession nodes, newest first)
    MATCH (ms:MemorySession) RETURN ms ORDER BY ms.created_at DESC LIMIT 20

Write tests: link_temporal_chain with a mock that has a prior meeting
(assert edge created) and without (assert returns False). decay_relevance
with mocked session (assert correct Cypher is run). log_memory_session
with mocked driver (assert MemorySession node + ACCESSED edges). detect_causality
with follow_up_needed=False (assert returns 0 without calling LM Studio).
```

---

## PHASE 24 — Procedural memory

```
Read CLAUDE.md and the design doc in full.

Update scripts/setup_memgraph.py — add seeded Procedure + ProcedureStep nodes
AFTER the index creation, using MERGE so it's idempotent on re-run.
Seed exactly these six procedures with their steps:

  sprint_planning:
    Steps (in order): 1. Review previous sprint outcomes
                      2. Groom and prioritise backlog
                      3. Estimate story points
                      4. Commit to sprint goal
                      5. Assign tasks to team members

  client_review:
    Steps: 1. Present deliverables
           2. Collect client feedback
           3. Document decisions and change requests
           4. Agree on next steps

  one_on_one:
    Steps: 1. Check-in on priorities
           2. Surface blockers
           3. Career development discussion
           4. Agree on action items

  incident_response:
    Steps: 1. Confirm incident and severity
           2. Assemble response team
           3. Diagnose root cause
           4. Apply fix or mitigation
           5. Write post-mortem

  project_kickoff:
    Steps: 1. Introduce stakeholders
           2. Define project scope and goals
           3. Agree on timeline and milestones
           4. Assign ownership
           5. Schedule follow-up checkpoints

  retrospective:
    Steps: 1. What went well
           2. What could be improved
           3. Action items for next iteration

  For each procedure, MERGE the Procedure node with:
    id = uuid5_id("procedure", name)
    name, description, is_inferred = false, occurrence_count = 0
    match_pattern = JSON string of the criteria dict (for reference/debugging)

  MERGE ProcedureStep nodes with HAS_STEP and NEXT_STEP edges.
  Use a consistent id: uuid5_id("step", f"{procedure_name}:{order}")

Create transform_service/procedural_memory.py:

  KNOWN_PROCEDURE_PATTERNS: dict mapping procedure name to a match dict:
    {
      "sprint_planning": {
          "kind": ["meeting"],
          "min_attendees": 3,
          "topic_keywords": ["sprint", "backlog", "velocity", "story points", "standup"]
      },
      "client_review": {
          "topic_keywords": ["client", "demo", "feedback", "presentation", "review"],
          "requires_multi_org": True
      },
      "one_on_one": {
          "max_attendees": 2,
          "min_attendees": 2
      },
      "incident_response": {
          "topic_keywords": ["incident", "outage", "bug", "hotfix", "urgent", "down", "p0", "p1"]
      },
      "project_kickoff": {
          "topic_keywords": ["kickoff", "onboarding", "new project", "launch", "initiation"]
      },
      "retrospective": {
          "topic_keywords": ["retro", "retrospective", "what went well", "improvements", "lessons"]
      }
    }

  def _matches_pattern(meeting: ExtractedMeeting, pattern: dict) -> bool:
    """Pure Python — no async, no Cypher. Check meeting against one pattern dict.
    keyword matching is case-insensitive substring matching against meeting.topics."""
    ...

  async def match_to_procedure(meeting: ExtractedMeeting, meeting_id: str,
                                attendee_emails: list[str]) -> list[str]:
    """Check meeting against all known patterns. For each match:
      - MERGE (m:Meeting)-[:FOLLOWS_PROCEDURE {confidence: 0.8}]->(p:Procedure)
      - Increment Procedure.occurrence_count by 1
    Returns list of matched procedure names."""
    matched = []
    for proc_name, pattern in KNOWN_PROCEDURE_PATTERNS.items():
      if _matches_pattern(meeting, pattern):
          proc_id = uuid5_id("procedure", proc_name)
          # MERGE the edge and increment count in one Cypher statement
          ...
          matched.append(proc_name)
    return matched

  async def discover_procedures() -> dict:
    """Nightly job. Find groups of meetings with:
      - same community_id (persons who share a community)
      - >= 60% topic overlap between meetings in the group
      - >= 5 meetings that haven't been matched to any Procedure yet
    For each qualifying group: create a new Procedure {is_inferred: True}
    and MERGE FOLLOWS_PROCEDURE edges to the meetings.
    
    Implementation approach:
      1. MATCH meetings not yet linked to any Procedure (no FOLLOWS_PROCEDURE edge)
         grouped by community_id of their attendees
      2. Within each community group, load their topic lists into Python
      3. For each pair of meetings in the group, compute overlap via
         graph_algorithms.get_jaccard_similarity() on their Topic neighbors
         OR simple Python set intersection on topic name lists (simpler, use this)
      4. Cluster meetings where average pairwise overlap >= 0.6
      5. If cluster size >= 5 and no existing Procedure matches:
           create Procedure node with:
             name = f"inferred_{community_id}_{cluster_index}"
             description = f"Inferred pattern from {len(cluster)} meetings"
             is_inferred = True
             occurrence_count = len(cluster)
           MERGE FOLLOWS_PROCEDURE edges to all cluster meetings
    Return dict: {clusters_found, procedures_created}"""
    ...

Wire into transform_service/graph_builder.py:
  After episodic_memory calls:
    from transform_service import procedural_memory
    emails = [a.email for a in meeting.attendees if a.email]
    matched = await procedural_memory.match_to_procedure(meeting, node_id, emails)
    if matched:
        log.info("graph_builder.procedures_matched", procedures=matched)
  Same try/except wrapper.

Add to nightly APScheduler in main.py:
  scheduler.add_job(procedural_memory.discover_procedures, "cron",
                    hour=2, minute=45, id="nightly_discover_procedures")

Add to main.py:
  GET /graph/procedures — list all Procedure nodes with occurrence_count,
    is_inferred, and their ProcedureStep names in order
  GET /graph/procedures/{procedure_name} — detailed view: steps + meetings that
    followed this procedure (MATCH (m)-[:FOLLOWS_PROCEDURE]->(p) RETURN m)

Write tests: _matches_pattern with matching and non-matching meeting fixtures
(no async, no mocks needed — pure Python). match_to_procedure: mock driver,
assert correct MERGE Cypher is called for a sprint_planning meeting. discover_procedures:
mock driver returning a set of meetings with known topics, assert Procedure node
created when cluster criteria met, not created when cluster too small.
```

---

## PHASE 25 — Memory retrieval + docs

```
Read CLAUDE.md and the design doc in full.

Create transform_service/memory_retrieval.py:

  Reuse extractor._get_client() — do NOT create a new LLM client.

  async def _extract_entities(question: str) -> dict:
    """Short LM Studio call. Extract structured entities from the question.
    Prompt system: "Extract entities from this question. Respond ONLY with JSON:
    {\"people\": [\"name\"], \"topics\": [\"keyword\"], \"date_hint\": \"string or null\"}
    Be conservative — only extract clearly named entities."
    Returns dict with people, topics, date_hint. On parse failure: return
    {"people": [], "topics": [], "date_hint": None}. Do NOT raise."""
    ...

  async def _assemble_context(entities: dict) -> tuple[dict, list[str]]:
    """Query the graph for relevant nodes. Returns (context_dict, node_ids_used).
    Cap at 20 total nodes to avoid LM context overflow.
    
    For each person name in entities["people"]:
      MATCH (p:Person) WHERE p.name CONTAINS $name
      RETURN p + p.pagerank_score + p.community_id +
             [(p)-[:HAS_FACT]->(f) | f.text][..5] +   // up to 5 facts
             [(p)-[:PREFERS]->(pref) | pref.value][..3] +  // 3 preferences
             [(p)-[:ATTENDED]->(m:Meeting) | m] ORDER BY m.relevance_weight DESC [..3]
    
    For each topic in entities["topics"]:
      MATCH (t:Topic {name: $topic})-[:DISCUSSED]-(m:Meeting)
      RETURN t + meetings ORDER BY m.relevance_weight DESC LIMIT 3

    Return context_dict structured as:
      {
        "people": [
          {"name": ..., "email": ..., "pagerank_score": ..., "community_id": ...,
           "facts": [...], "preferences": [...], "recent_meetings": [...]}
        ],
        "topics": [{"name": ..., "recent_meetings": [...]}],
        "algorithm_summary": "Top person by PageRank: X. Found N communities."
      }
    node_ids_used = all node IDs accessed across all queries."""
    ...

  async def _synthesize(question: str, context: dict) -> str:
    """Call LM Studio with assembled graph context.
    System: "You are a meeting memory assistant with access to a structured
    knowledge graph. Answer the question using ONLY the context below.
    Be specific and cite names and dates when available. If the context
    does not contain enough information, say so — do not guess.
    Context: {json.dumps(context, default=str, indent=2)}"
    User: question
    Returns the model's answer string. On API error, return a graceful
    fallback message — do NOT raise."""
    ...

  async def full_memory_query(question: str) -> dict:
    """Orchestrate the three steps. Always returns a dict even on partial failure.
    After synthesis: call episodic_memory.log_memory_session() to record the session.
    Returns:
      {
        "answer": str,
        "session_id": str,
        "nodes_used": [{"id": ..., "type": ...}],
        "context_summary": {"people_found": N, "topics_found": N}
      }"""
    ...

  async def person_memory_profile(email: str) -> dict:
    """Return the full memory profile for one person:
      - semantic: facts (HAS_FACT), preferences (PREFERS), KNOWS connections
      - episodic: last 10 meetings with relevance_weight, PRECEDED_BY chain depth
      - procedural: Procedures this person's meetings follow (via FOLLOWS_PROCEDURE)
      - algorithms: pagerank_score, community_id, betweenness_centrality
    All from Cypher queries — no LM call. Returns {} if person not found."""
    ...

Add endpoints to transform_service/main.py:

  POST /graph/memory/query
    Body: {"question": str}
    Response: full_memory_query result dict
    On empty question: 400 Bad Request.

  GET /graph/memory/person/{email}
    → person_memory_profile(email)
    On not found: 404 with detail.

Update CLAUDE.md:
  Add a new section "## Graph Memory + Advanced Algorithms" after the
  "## Autonomous Dev Agent" section:

    - graph_algorithms.py is the ONLY place for MAGE CALL procedures.
      Never embed CALL module.procedure() in memgraph_client.py, graph_builder.py,
      main.py, or any memory module.
    - semantic_memory.py owns Fact and Preference nodes.
    - episodic_memory.py owns PRECEDED_BY, CAUSED_BY, MemorySession nodes,
      and the relevance_weight decay.
    - procedural_memory.py owns Procedure and ProcedureStep nodes.
    - memory_retrieval.py is the only module that calls full_memory_query and
      person_memory_profile — never call these from graph_builder.py or main.py
      directly (those are query-time, not ingestion-time).
    - All LM Studio calls in memory modules reuse extractor._get_client() —
      no new AsyncOpenAI instances.

  Add to Absolute Rules:
    DO NOT add MAGE CALL procedures outside graph_algorithms.py
    DO NOT call memory_retrieval functions during ingestion (graph_builder.py)
    DO NOT write MemorySession nodes outside episodic_memory.log_memory_session()

Create docs/GRAPH_MEMORY.md, matching the style of docs/DEV_AGENT.md:
  1. Overview of the three memory types and what they store in this project
  2. How to query the memory: POST /graph/memory/query examples
  3. How algorithm scores are computed and when they update
  4. How procedures are matched (known) and discovered (inferred)
  5. The MCP server already exposes all of this — any Claude Desktop query
     like "who are the most influential people in my meetings?" now works
     because pagerank_score is a queryable node property
  6. Schema diagram (text-based, ASCII) showing new node types and edges

Write tests: _extract_entities with valid JSON from mocked LM Studio,
invalid JSON from mocked LM Studio (assert returns empty dict, no raise).
full_memory_query: mock _extract_entities, _assemble_context, _synthesize,
log_memory_session — assert all called and result dict has required keys.
person_memory_profile: mock driver — assert 404 behavior when no person found.
```

---

## Notes on running Phases 21-25

- Run Phase 21 FIRST and verify MAGE is available before any other phase:
    make cypher  # then run: CALL mg.procedures() YIELD name WHERE name STARTS WITH 'pagerank' RETURN name;
  If that returns nothing, stop — the image doesn't have MAGE bundled and you
  need to investigate before proceeding.
- Phases 22-24 can only be wired into graph_builder.py after Phase 21 is done
  (they depend on graph_algorithms being imported and working).
- Phase 25 depends on all of 21-24 (it queries nodes written by all of them).
- After each phase: `make test` before proceeding. After Phase 21: also run
  `make trigger` and check `make logs` to confirm the fast algorithm run fires
  after a processed meeting.
- The nightly jobs (full recompute, consolidation, decay, discovery) will NOT
  fire during dev — test them by calling the functions directly via make trigger
  or a manual curl to the /webhook/airbyte endpoint, then checking Memgraph.
- Procedures seeded in setup_memgraph.py need `make setup-memgraph` to be re-run
  after Phase 24's changes land (it's idempotent — safe to re-run on a live graph).
- Superpowers will trigger TDD automatically — let it. Don't skip tests.
- If a phase feels too big, use /plan from forge-skills to break it down further.
