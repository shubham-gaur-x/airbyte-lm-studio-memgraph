# airbyte-lm-studio-memgraph

> Meeting memory pipeline — v4. Fully local. LM Studio + Memgraph + Airbyte.

## What This Is

A local-first meeting intelligence pipeline. Ingests Gmail, Google Calendar, and Jira
via Airbyte Cloud, extracts structured data with a local LLM (LM Studio + Gemma3:12b),
and stores everything as a property graph in local Memgraph.

**Everything runs on your Mac via `docker compose up`.** No cloud services needed at demo time
(except Airbyte Cloud for ingestion and Jira for ticketing).

## Architecture

```
Gmail / Google Calendar / Jira
         │
         ▼  Airbyte Cloud (connectors)
         │
         ▼  Local Postgres (Docker) ← staging
         │
         ▼  Transform Service (Docker)
              classifier → LM Studio (gemma3:12b) → Memgraph
                                                   → Jira (sprint)
         │
         ▼  Local Memgraph (Docker)
              + MCP Server → Claude Desktop / agents
         │
         ▼  FastAPI (/timeline, /digest, /actions)
```

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/shubham-gaur-x/airbyte-lm-studio-memgraph
cd airbyte-lm-studio-memgraph
cp .env.example .env  # fill in secrets

# 2. Start LM Studio on your Mac, load gemma3:12b

# 3. Start all services
make up

# 4. Run smoke test
make smoke-test

# 5. Open Memgraph Lab
open http://localhost:3000
```

## Stack

| Component | Tool | Notes |
|---|---|---|
| Ingestion | Airbyte Cloud | Gmail, Calendar, Jira connectors |
| Staging | Local Postgres (Docker) | raw_emails, raw_jira_issues |
| LLM | LM Studio + Gemma3:12b | Local, OpenAI-compatible API |
| Graph DB | Local Memgraph (Docker) | ACID transactions |
| Graph MCP | Memgraph MCP Server | Claude Desktop + agent queries |
| Ticketing | Jira | ActionItems → sprint + read back |
| API | FastAPI | /timeline, /digest, /actions |

## What's New vs v3

- LM Studio replaces Groq (local inference, no data leaves Mac)
- Local Memgraph replaces Memgraph Cloud
- Local Postgres replaces Neon
- Memgraph MCP server for natural language graph queries
- Bidirectional Jira (write AND read via Airbyte)
- Timeline view: `/graph/timeline?window=day|week|month`
- ACID-compliant graph writes (batched transactions)

## Development

```bash
make logs       # tail transform service logs
make cypher     # open Memgraph console
make psql       # open Postgres console
make backfill   # reprocess all unprocessed records
make reset-db   # wipe and restart all data
```

## Claude Code Setup

```
/plugin install superpowers@claude-plugins-official
/plugin marketplace add aneja5/forge-skills
/plugin install forge-skills@forge-skills
```

Then follow phases in `prompts/PROMPTS.md`.

## Related

- v3 (cloud): `shubham-gaur-x/airbyte-meeting` — do not modify
