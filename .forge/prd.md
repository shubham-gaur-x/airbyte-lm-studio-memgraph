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

---

## Addendum (2026-06-29): Autonomous Jira Dev Agent

See docs/superpowers/specs/2026-06-29-autonomous-dev-agent-design.md for full design.

### Problem
Jira tickets require a human to select, start, implement, and close every one.

### Solution
A new `dev_agent` service runs the entire lifecycle with no human selection step:
it triages `BACKLOG` tickets (no `meeting-action-item` label, real description)
to `TO DO` itself, implements them against this repo via headless Claude Code
pointed at LM Studio (not the Anthropic API), moves them to `IN PROGRESS` while
working, runs tests, opens a verified PR, and moves them to `IN REVIEW`.
Action items are classified `is_engineering_task` at extraction time, so
non-coding meeting follow-ups ("schedule a call") are tagged and never enter
this pipeline at any stage — engineering ones flow through with no human in
the loop at all until the PR needs merging.

### Non-Goals (this phase)
- Auto-merging PRs — the one remaining human checkpoint, deliberately deferred
- Any other target repo
- Any Anthropic API usage or billing
- Auto-implementing non-engineering action items

### Success Criteria
- A `BACKLOG` ticket with a real description and no skip label reaches an
  open, verified PR with zero human interaction at any stage
- The board visibly shows `BACKLOG → TO DO → IN PROGRESS → IN REVIEW` in
  real time
- A ticket the agent can't complete returns to `TO DO` (not stuck in
  `IN PROGRESS`) and does not retry indefinitely
- Zero Anthropic API usage anywhere in this flow
