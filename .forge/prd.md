# PRD — airbyte-lm-studio-memgraph (v4)

forge:meta
  version: 1
  produced_by: brainstorming + architecture-and-contracts
  inputs: [CLAUDE.md, conversation context]

---

## Problem

v3 of the meeting-memory pipeline runs on cloud services (Render, Groq, Memgraph Cloud).
This creates demo reliability issues, cost concerns, and data privacy concerns.
Matteo wants the pipeline fully local so demos work without internet dependency and
no meeting data leaves the Mac.

## Solution

A fully local Docker Compose stack that:
- Replaces Groq with LM Studio (local Gemma3:12b, OpenAI-compatible)
- Replaces Memgraph Cloud with local Memgraph (Docker)
- Replaces Neon Postgres with local Postgres (Docker)
- Adds Memgraph MCP server for agent/LLM graph access
- Adds bidirectional Jira flow (write AND read via Airbyte)
- Adds timeline view (day/week/month graph filter)
- Keeps Airbyte Cloud as the ingestion backbone (important for Airbyte team demo)

## Non-Goals

- Cloud deployment (that's v3)
- Slack connector (removed — wasn't providing signal)
- Multi-user access (single-developer setup)

## Success Criteria

- `docker compose up` starts all services in < 60 seconds
- Sample email → extracted meeting in Memgraph in < 30 seconds (LM Studio)
- `make smoke-test` passes green
- Claude Desktop can query the graph via MCP in natural language
- Demo guide works end-to-end without internet (except Airbyte Cloud + Jira)

## Constraints

- M2 Pro 16GB — Gemma3:12b Q4_K_M is the max model size
- Must keep Airbyte Cloud (showcase requirement)
- Must keep Jira (action items → sprint routing is core feature)
- v3 repo must not be touched
