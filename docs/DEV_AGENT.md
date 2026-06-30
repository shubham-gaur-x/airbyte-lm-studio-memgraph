# Autonomous Dev Agent

The dev agent is a fully autonomous Jira ticket implementer that runs as a Docker service alongside the rest of the local stack. It polls Jira, promotes eligible backlog tickets to TO DO, implements them using headless Claude Code pointed at LM Studio, and opens pull requests — with no human involvement at any stage before the PR. The design rationale for every decision here is in [`docs/superpowers/specs/2026-06-29-autonomous-dev-agent-design.md`](superpowers/specs/2026-06-29-autonomous-dev-agent-design.md).

**This is the fully autonomous revision:** no human selects which BACKLOG tickets get worked. The agent triages BACKLOG → TO DO itself based on a JQL query (non-empty description, no `meeting-action-item` label). The one remaining human checkpoint is merging the PR — that is deliberately not automated yet.

---

## Prerequisites

Before the dev agent can run end-to-end:

1. **LM Studio 0.4.1 or later** running on the host Mac with a model loaded.
   - Raise the model's context length to at least **25K tokens** in LM Studio → server/model settings → Context Length. Claude Code is context-heavy; LM Studio's default is too small and will cause mid-run truncation.
   - LM Studio 0.4.1+ exposes a native Anthropic-compatible endpoint at `http://localhost:1234` (separate from the OpenAI-compatible `/v1` path). The dev agent uses this endpoint via `ANTHROPIC_BASE_URL`.

2. **GitHub personal access token** with `repo` scope in `.env` as `GITHUB_TOKEN`.

3. **`GITHUB_OWNER` and `GITHUB_REPO`** in `.env` matching this repository (default: `shubham-gaur-x` / `airbyte-lm-studio-memgraph`).

4. The rest of the stack running: `make up` (Postgres healthcheck required by `dev_agent`).

---

## Manual test path (without waiting for the poll cycle)

```bash
# Promote all eligible BACKLOG tickets to TO DO right now
make dev-agent-triage

# Trigger implementation of a specific ticket immediately (bypasses filters)
make dev-agent-trigger TICKET=SCRUM-123

# Check recent run history
make dev-agent-runs

# Stream dev agent logs
make dev-agent-logs
```

`/trigger/<key>` bypasses the TO DO filter and label check — useful for testing against a known ticket without having to wait for triage.

---

## Known limitation: Gemma loop behaviour

Gemma-family models have documented issues getting stuck repeating the same tool call inside Claude Code. If a run consistently hits `--max-turns` without producing a PR on tickets that look implementable:

- Load a different model in LM Studio (e.g. a Qwen or Mistral variant).
- Update `DEV_AGENT_LM_MODEL` in `.env` to the new model name.
- Restart the dev agent: `docker compose restart dev_agent`.

No code rebuild needed — the model name is read from env at each poll cycle.

---

## Full autonomous lifecycle

```
BACKLOG  →  TO DO  →  IN PROGRESS  →  IN REVIEW
         ↑          ↑                 (PR merged by human)
       triage    agent starts
     (autonomous)  implementation
                  (autonomous)
```

**There is no human checkpoint before TO DO or before IN PROGRESS.** Any ticket placed in BACKLOG with a real description and no `meeting-action-item` label will be autonomously promoted and implemented.

Tickets labeled `meeting-action-item` are never touched at any stage — this label is set by `jira_pusher.py` for process action items (scheduling, communication, non-engineering follow-ups) and serves as the permanent skip signal.

---

## What happens on failure

When the agent cannot complete a ticket:

1. The ticket transitions back to **TO DO** (not left stuck in IN PROGRESS).
2. A Jira comment is posted: `"Dev agent could not complete this ticket automatically. Needs human follow-up."`
3. `dev_agent_runs` records `status=failed` with an error message.
4. The run will **not** be retried automatically unless `DEV_AGENT_MAX_ATTEMPTS` is raised above 1 in `.env`.

Check `make dev-agent-runs` to see the full run history with error details.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub PAT with `repo` scope |
| `GITHUB_OWNER` | `shubham-gaur-x` | GitHub org/user |
| `GITHUB_REPO` | `airbyte-lm-studio-memgraph` | Target repo |
| `LM_STUDIO_ANTHROPIC_URL` | `http://host.docker.internal:1234` | LM Studio native Anthropic endpoint |
| `DEV_AGENT_LM_MODEL` | `gemma3-12b` | Model name passed to `claude --model` |
| `DEV_AGENT_BACKLOG_STATUS` | `Backlog` | Jira status to pull from in triage |
| `DEV_AGENT_TODO_STATUS` | `To Do` | Target status after triage |
| `DEV_AGENT_IN_PROGRESS_STATUS` | `In Progress` | Status set when implementation starts |
| `DEV_AGENT_REVIEW_STATUS` | `In Review` | Status set when PR is verified |
| `DEV_AGENT_SKIP_LABELS` | `meeting-action-item` | Comma-separated labels that skip a ticket |
| `DEV_AGENT_POLL_MINUTES` | `10` | How often the agent polls Jira |
| `DEV_AGENT_BATCH_SIZE` | `5` | Max tickets attempted per poll cycle |
| `DEV_AGENT_MAX_TURNS` | `40` | Max Claude Code turns per ticket |
| `DEV_AGENT_TIMEOUT_SECONDS` | `1800` | Hard wall-clock timeout per ticket (30 min) |
| `DEV_AGENT_MAX_ATTEMPTS` | `1` | Max retries per ticket before permanent skip |
| `DEV_AGENT_GIT_NAME` | `Meeting Memory Dev Agent` | Git author name for commits |
| `DEV_AGENT_GIT_EMAIL` | `dev-agent@local` | Git author email for commits |
