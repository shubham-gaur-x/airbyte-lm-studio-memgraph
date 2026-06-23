# Design: airbyte-lm-studio-memgraph v4

**Date:** 2026-06-22
**Author:** Shubham Gaur
**Status:** Approved — implementation in progress

---

## Problem

v3 of the meeting-memory pipeline runs on cloud services (Render, Groq, Memgraph Cloud), creating demo reliability issues, cost concerns, and data-privacy concerns. v4 replaces all cloud services with a fully local Docker Compose stack while keeping Airbyte Cloud as the visible ingestion backbone (important for Airbyte team demo).

## Solution

A fully local Docker Compose stack running on MacBook M2 Pro 16GB:

```
Gmail / Google Calendar / Jira
  → Airbyte Cloud (3 connectors, incremental, Append+Dedup)
  → Local Postgres (Docker) — staging tables + processed_flag
  → Transform Service (FastAPI, Docker)
      classifier.py     rules-based meeting scorer
      extractor.py      LM Studio gemma3:12b (OpenAI-compat API)
      graph_builder.py  pipeline orchestrator
      jira_pusher.py    ActionItems → Jira sprint
      jira_agent.py     Jira status → Memgraph (bidirectional, NEW)
      digest.py         weekly summary
  → Local Memgraph (Docker) — ACID transactions, 6 node types
  → Memgraph MCP Server (Docker sidecar) — Claude Desktop / agents
  → FastAPI endpoints (/timeline, /digest, /actions, /health, webhooks)
```

## Architecture Decisions

### LLM: LM Studio (not Ollama/Groq)
- OpenAI-compatible API at `http://host.docker.internal:1234/v1`
- Model: gemma3-12b-Q4_K_M (~7-8GB, fits M2 Pro 16GB)
- `openai.AsyncOpenAI(base_url=..., api_key="lm-studio")`
- temperature=0.0, response_format=json_object, max_tokens=2000

### Graph DB: Local Memgraph (not Memgraph Cloud)
- neo4j async driver, bolt://memgraph:7687
- All multi-node writes in single Cypher transaction (ACID)
- MERGE everywhere (never CREATE for unique nodes)
- All nodes: id (uuid5 deterministic), created_at, updated_at

### Staging: Local Postgres (not Neon)
- asyncpg connection pool
- Tables: raw_emails, raw_calendar_events, raw_jira_issues
- processed BOOLEAN column for exactly-once semantics
- APScheduler polls every 5 min + webhook trigger on Airbyte sync

## Graph Schema

**Nodes:** Meeting · Person · Organization · Topic · Decision · ActionItem

**Edges:** ATTENDED · DISCUSSED · PRODUCED · ASSIGNED_TO · WORKS_AT · FOLLOWS_UP · MENTIONS

**Indexes:** UNIQUE on Meeting.id, Person.email, Topic.name, Decision.id, ActionItem.id, Organization.domain. B-tree on Meeting.date, Meeting.created_at, ActionItem.created_at.

## Jira Bidirectional Flow

**Write direction (v3 carry-over):** jira_pusher.py creates Jira issues for ActionItems. High priority → active sprint. Medium/low → backlog. Calls update_action_jira_key() after creation.

**Read direction (NEW v4):** Airbyte Jira source → raw_jira_issues → jira_agent.py → update ActionItem.jira_status in Memgraph. Closes the loop.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /webhook/airbyte | Trigger pipeline on Airbyte sync complete |
| GET | /health | Ping all services |
| GET | /graph/meetings/recent | Last 10 meetings |
| GET | /graph/person/{email} | Person + their meetings/actions |
| GET | /graph/topic/{name} | Topic + meetings that discussed it |
| GET | /graph/actions/open | Open action items |
| GET | /graph/timeline?window=day\|week\|month | NEW: time-windowed graph view |
| GET | /graph/digest/weekly | Structured weekly summary |

## Module Boundaries

- **No Cypher outside `memgraph_client.py`**
- **No SQL outside `db.py`**
- **No cloud LLM calls** (only LM Studio via openai SDK)
- `httpx.AsyncClient` for all HTTP — never `requests`
- `@with_retry(max_attempts=3, base_delay=2.0)` on all external calls
- `structlog` — every log includes source, meeting_id, step
- Python 3.11+, Pydantic v2, type hints on ALL signatures

## Success Criteria

- `docker compose up` starts all services in < 60 seconds
- Sample email → extracted meeting in Memgraph in < 30 seconds
- `make smoke-test` passes green
- Claude Desktop queries graph via MCP in natural language
- Demo works without internet (except Airbyte Cloud + Jira)

## Non-Goals

- Cloud deployment (that's v3)
- Slack connector (removed — wasn't providing signal)
- Multi-user access

## Implementation Phases

| Phase | Deliverable |
|-------|-------------|
| 0 | Scaffold (complete) |
| 1 | docker-compose.yml + Makefile (complete) |
| 2 | models.py + utils.py |
| 3 | db.py |
| 4 | memgraph_client.py |
| 5 | classifier.py |
| 6 | extractor.py |
| 7 | graph_builder.py |
| 8 | jira_pusher.py |
| 9 | jira_agent.py |
| 10 | main.py |
| 11 | scripts/ |
| 12 | docs/AIRBYTE_SETUP.md |
| 13 | docs/DEMO_GUIDE.md |
