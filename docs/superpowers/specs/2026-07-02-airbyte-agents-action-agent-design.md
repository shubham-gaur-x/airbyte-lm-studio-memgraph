# Design: Airbyte Agents Action Agent

**Date:** 2026-07-02
**Author:** Shubham Gaur (planned with Claude)
**Status:** Approved — implementation not yet started

---

## Problem

Meeting action items that are not engineering tasks (e.g. "Follow up with Matteo
to reschedule", "Share Airbyte webhook docs", "Review budget adjustments") are
pushed to Jira by `jira_pusher.py` with the `meeting-action-item` label — and
then nothing happens. `dev_agent` deliberately skips them (they are not code
tasks), so they sit in **To Do** untouched. The pipeline creates work but never
works it.

Separately, the demo story is incomplete: the project showcases Airbyte's ELT
product but not **Airbyte Agents** (app.airbyte.ai), Airbyte's agent tool
platform. The v3 architecture diagram imagined an "Airbyte Agents" box that was
never built; the v4 spec quietly dropped it. This design brings it back — as
what the product actually is, not what the v3 diagram imagined.

## What Airbyte Agents Actually Is (research finding)

Airbyte Agents is **not** a hosted autonomous agent runtime. It is a tool and
context layer: 60+ typed Python connectors (Jira, GitHub, …) with managed
credentials, exposed to *your own* agent via a Python SDK
(`airbyte-agent-sdk`), CLI, HTTP API, or MCP server. You bring the LLM.

Consequences for this design:

- **No conflict with the no-cloud-LLM rule.** The reasoning LLM stays LM Studio
  (local). Airbyte Agents supplies the Jira tools the agent calls.
- **The trigger is ours.** The platform does not watch for new Jira tickets;
  our existing APScheduler/webhook machinery does that.
- The Jira agent connector (v1.2.0) supports full read/write: search issues,
  get detail, create/update comments, transition status.
- In **hosted mode** (credentials managed in the app.airbyte.ai workspace),
  every tool call the agent makes is logged in the platform's Sessions / Tool
  Calls dashboard — the demo payoff.

## Decisions (from design discussion)

1. **Scope:** the agent owns exactly the tickets `dev_agent` skips —
   non-engineering, `meeting-action-item`-labeled tickets. Complementary, zero
   overlap: `dev_agent` writes code, `action_agent` handles everything else.
2. **Output & terminal state:** for each ticket the agent pulls related graph
   context, drafts the actual deliverable (the follow-up message, the doc
   summary, the plan) as a Jira comment, and transitions **To Do → In Review**.
   A human glances, acts, and clicks Done — the same human-checkpoint
   philosophy as `dev_agent`'s PR merge gate.
3. **Hosted mode, dashboard-visible:** the Jira connector is registered in the
   app.airbyte.ai workspace (org Onix) so agent activity appears in Sessions /
   Tool Calls. Ticket data flowing through Airbyte's cloud tool layer is
   consistent with ELT already syncing full Jira content through Airbyte Cloud.

## Non-Goals

- No autonomous "mark Done" — In Review is the terminal state the agent may set.
- No replacement of `jira_client.py` REST calls in `transform_service` or
  `dev_agent` — this is purely additive.
- No true tool-calling LLM loop (LLM choosing tools dynamically). The pipeline
  is fixed; the LLM only drafts. Revisit only if gemma3-12b function calling
  proves reliable.
- No MCP + headless Claude Code variant — blocked by LM Studio's context
  length (proven 2026-07-02 with dev_agent).
- No sending of emails/messages on the user's behalf. The agent drafts; humans
  send.

## Architecture

One new module: `transform_service/action_agent.py`, running inside the
existing `transform_service` process. No new Docker service. New dependency:
`airbyte-agent-sdk` in `transform_service/requirements.txt`.

`action_agent.py` is the ONLY place `airbyte-agent-sdk` appears — mirroring
`graph_algorithms.py` (only MAGE), `db.py` (only SQL), `jira_client.py` (only
Jira REST).

### Pipeline (per run)

```
1. FIND      Airbyte SDK Jira search:
             project={JIRA_PROJECT_KEY} AND status="To Do"
             AND labels=meeting-action-item
             capped at ACTION_AGENT_BATCH_SIZE (default 5)
2. GUARD     Airbyte SDK lists the ticket's comments; if ACTION_AGENT_MARKER
             is already present (comment landed, transition failed last run),
             skip straight to step 6
3. CONTEXT   memory_retrieval.full_memory_query(ticket summary + description)
             → related people, meetings, facts, decisions from Memgraph
4. DRAFT     One LM Studio call (extractor._get_client(), temp 0.0):
             ticket + graph context → deliverable text
5. COMMENT   Airbyte SDK posts the draft, prefixed with ACTION_AGENT_MARKER
6. TRANSITION Airbyte SDK moves To Do → In Review
```

### Triggers (three, all landing on the same run function)

- APScheduler job every 5 minutes, registered in `main.py` lifespan
- Background task queued by the existing `POST /webhook/airbyte` handler
  (same pattern as `process_new_emails`)
- Manual `POST /agent/actions/run` endpoint for demos

**Boundary rule:** `action_agent` must NEVER be called from `graph_builder.py`.
It consumes `memory_retrieval`, which is banned during ingestion. The
scheduler/webhook-background-task/manual triggers respect this; an inline
"process right after jira_pusher creates the ticket" hook is explicitly
forbidden, however natural it seems.

### Idempotency & failure semantics

- Status is the primary guard: only **To Do** tickets are eligible.
- Partial-failure repair: before drafting, each run checks the ticket's
  comments for `ACTION_AGENT_MARKER`. If present (comment posted but
  transition failed last run), skip straight to the transition — never
  double-comment.
- Per-ticket try/except: one bad ticket never aborts the batch (house pattern).
- LLM failure → no Jira writes at all; ticket stays To Do; logged; retried
  next poll.
- `@with_retry(max_attempts=3, base_delay=2.0)` on SDK and LM Studio calls.
- `ACTION_AGENT_ENABLED=false` short-circuits everything (like `JIRA_ENABLED`).

### Draft comment format

```
{ACTION_AGENT_MARKER}

{deliverable text}

—
Drafted by action_agent (LM Studio, local) via Airbyte Agent SDK.
Graph context: {n} nodes consulted.
```

`ACTION_AGENT_MARKER` is the module-level constant `"[action-agent draft]"`.
It must never change once tickets carry it — the idempotency guard greps
comments for this exact string.

## Configuration

New env vars (in `.env` and `.env.example`):

```env
# Action Agent (Airbyte Agents SDK — see app.airbyte.ai, org Onix)
ACTION_AGENT_ENABLED=true
ACTION_AGENT_BATCH_SIZE=5
AIRBYTE_AGENTS_CLIENT_ID=
AIRBYTE_AGENTS_CLIENT_SECRET=
```

**Credential collision note:** `.env` already uses
`AIRBYTE_CLIENT_ID`/`AIRBYTE_CLIENT_SECRET` for the Airbyte Cloud **ELT** API
(cloud.airbyte.com) — a different product. The SDK's env autodiscovery reads
those exact names, so `action_agent.py` passes the new
`AIRBYTE_AGENTS_*` values to the SDK client explicitly via constructor,
never relying on autodiscovery.

### Manual setup (user-only, in app.airbyte.ai org Onix)

1. Add the **Jira connector** ("Add Connector"): Jira domain + API token.
   Hosted mode — Airbyte stores the credential.
2. Generate SDK **client credentials** in workspace settings; paste into
   `.env` as `AIRBYTE_AGENTS_CLIENT_ID` / `AIRBYTE_AGENTS_CLIENT_SECRET`
   (user types these directly into the file; never through chat).

## Module Boundaries (extends CLAUDE.md rules)

- `action_agent.py` owns: all `airbyte-agent-sdk` usage, the pipeline, the
  marker constant.
- `action_agent.py` uses: `memory_retrieval.full_memory_query` (the sanctioned
  query-time consumer), `extractor._get_client()`, `utils.strip_json_fences`,
  `utils.with_retry`, structlog.
- `action_agent.py` never touches: `jira_client.py`, `dev_agent/`,
  `graph_builder.py`, `memgraph_client.py`.
- Existing-file changes limited to: `main.py` (scheduler job, webhook
  background task, manual endpoint), `requirements.txt`, `.env.example`,
  CLAUDE.md.

### CLAUDE.md amendments

- Jira rule becomes: "DO NOT put Jira REST calls outside `jira_client.py`;
  DO NOT use the Airbyte Agent SDK outside `action_agent.py`."
- New absolute rule: "DO NOT call `action_agent` functions from
  `graph_builder.py` (it consumes memory_retrieval — query-time only)."

## Testing

`tests/test_phase27_action_agent.py`, everything mocked (SDK client, LM Studio
client, `memory_retrieval`):

1. Happy path: eligible ticket → context queried, draft posted with marker,
   transition to In Review called.
2. Marker already present → no second comment; transition called.
3. LLM failure → zero Jira writes; returns without raising.
4. Ineligible tickets (wrong status/label) filtered out.
5. Batch cap respected.
6. `ACTION_AGENT_ENABLED=false` → no-op.

Suite must stay green: 101 existing + new tests.

## Success Criteria

- A `meeting-action-item` ticket in To Do gets, within one poll cycle: a
  drafted deliverable comment grounded in real graph context, and lands in
  In Review.
- The run's Jira tool calls are visible in app.airbyte.ai → Sessions /
  Tool Calls.
- `SCRUM-47` ("Action requested (unspecified)", currently To Do with the
  label) is the live first test candidate.
- Zero regressions: full pytest suite green; `dev_agent` and ingestion
  behavior unchanged.

## Implementation Phases

| Phase | Deliverable |
|---|---|
| 27a | Manual setup (user): Jira connector + SDK credentials in app.airbyte.ai |
| 27b | `action_agent.py` + tests (SDK integration, pipeline, idempotency) |
| 27c | Wiring: `main.py` triggers, env, CLAUDE.md amendments, live end-to-end run against SCRUM-47 |
