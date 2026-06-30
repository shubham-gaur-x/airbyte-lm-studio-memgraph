# Design: Autonomous Jira Dev Agent

**Date:** 2026-06-29 (revised)
**Author:** Shubham Gaur (planned with Claude)
**Status:** Approved — implementation not yet started

---

## Problem

Jira tickets — both engineering tickets created directly and action items pushed by
`jira_pusher.py` from meetings — currently require a human at every stage: deciding a
ticket is ready to work (BACKLOG → TO DO), implementing it, testing it, and closing the
loop. The goal is a fully closed loop with no human required to select, start, or
implement work — only to merge the resulting PR, for now.

## Non-Goals (this phase)

- Auto-merging PRs — explicitly deferred. Output stops at an open, verified PR; a human
  merges it. (This is the one remaining checkpoint, kept deliberately for now.)
- Touching any repo other than `airbyte-lm-studio-memgraph`
- Any use of the Anthropic API or a Claude subscription — runs entirely on LM Studio
- Auto-implementing non-engineering action items (scheduling, communication, process
  tasks) — see Trigger Logic below for how those are identified and routed differently

## Solution

```
Meeting → extractor.py (LM Studio)
            ActionItem now classified: is_engineering_task: bool
  → graph_builder.py → Memgraph (unchanged, + is_engineering_task on ActionItem node)
  → jira_pusher.py
       is_engineering_task = True  → ticket created, no skip label
       is_engineering_task = False → ticket created, labeled meeting-action-item
  → Jira (new ticket lands in BACKLOG, Jira's default for new issues)

dev_agent polls every DEV_AGENT_POLL_MINUTES:

  1. TRIAGE  — BACKLOG, no meeting-action-item label, non-empty description
               → transition to TO DO
               (covers both freshly-created engineering action items AND any
               ticket a human creates directly in Jira — one mechanism, one
               place that decides "is this ready," same JQL-based query used
               for both)

  2. IMPLEMENT — TO DO, no meeting-action-item label, not already attempted
               (per dev_agent_runs) → for each, sequentially:
       a. transition TO DO → IN PROGRESS               (visible on the board
                                                          the moment work starts)
       b. git worktree, branch agent/<KEY>
       c. claude -p <prompt>   — Claude Code, headless, against LM Studio
            reads CLAUDE.md, implements, runs tests,
            git commit/push, gh pr create (base: main, head: agent/<KEY>)
       d. github_client.find_open_pr()    independent verification — never
                                           trust the model's own claim
       e. success → comment + transition IN PROGRESS → IN REVIEW
          failure → comment "needs human follow-up" + transition back to
                     TO DO (best-effort — don't leave a stale IN PROGRESS
                     ticket with nothing actually happening)
       f. dev_agent_runs updated either way (pr_opened / failed)
```

## Architecture Decisions

### Engineering vs. process classification happens at extraction, not at the Jira layer

The dev agent should never be asked to "implement" *"schedule a follow-up call with
Matteo."* Rather than trying to detect that after the fact from ticket text, `extractor.py`
classifies each action item at the point it already has full meeting context: add
`is_engineering_task: bool` to the `ActionItem` JSON schema in the LLM prompt and to the
`ActionItem` Pydantic model. `jira_pusher.py` reads this one field to decide whether to
apply the `meeting-action-item` skip label. Everything downstream (triage, dev agent)
only ever looks at the label — it doesn't need to know anything about meetings or
extraction. This keeps the classification decision where the context is, and keeps
`dev_agent` decoupled from `transform_service`'s extraction internals.

This is the same mechanism, reused, for human-created tickets: anyone can create a
ticket directly in Jira, and as long as it isn't labeled `meeting-action-item` and has a
real description, it's eligible — no separate "is this from a meeting" branch needed.

### Trigger: two-stage, both stages fully autonomous

`BACKLOG → TO DO` is no longer a human decision — it's a triage query (no skip label +
non-empty description). `TO DO → IN PROGRESS → IN REVIEW` was already autonomous in the
prior design; the only addition is making `IN PROGRESS` an explicit, visible transition
at the start of implementation rather than jumping straight to `IN REVIEW` at the end.

Both stages reuse `jira_client.list_eligible_tickets()` with different status/JQL
parameters — one function, two call sites, rather than separate search logic for each
stage.

### Jira leg, coding engine, GitHub leg, isolation, state tracking

Unchanged from the prior revision of this design — see git history of this file for the
full rationale on each. Summary: direct-REST `jira_client.py` (not the Airbyte Agent
SDK), headless Claude Code pointed at LM Studio's native Anthropic-compatible endpoint
(no Anthropic API key, no exception to the no-cloud-LLM rule), Claude Code creates its
own PR via `gh pr create` and we independently verify it via a read-only GitHub call,
one git worktree per ticket, `dev_agent_runs` (Postgres) preventing duplicate/repeat
processing.

## Module Boundaries (extends the existing rules)

- No Cypher outside `memgraph_client.py`; no SQL outside `db.py` / `dev_agent/db.py`
- No Jira REST calls outside `jira_client.py`
- The engineering/process decision lives in `extractor.py`'s output, nowhere else —
  `jira_pusher.py` and `dev_agent` only ever branch on the resulting label
- `dev_agent` never touches Memgraph or the extraction pipeline directly
- `httpx.AsyncClient` for all HTTP; `@with_retry(max_attempts=3, base_delay=2.0)` on all
  external calls; `structlog` everywhere; Python 3.11+, Pydantic v2, full type hints

## Safety Notes

Removing the BACKLOG → TO DO human gate means any ticket — including ones created by
anyone with Jira access, not just the meeting pipeline — can now trigger an autonomous
code change and PR with no human awareness until the PR shows up. The remaining
guardrails: PR-only output (a human still merges — this is intentionally the last
checkpoint, see Non-Goals), fresh branch only, explicit tool allowlist rather than
`--dangerously-skip-permissions`, and `dev_agent_runs`' attempt-limiting so a bad ticket
fails once and stops, rather than retrying indefinitely. A Jira ticket's free text is
still effectively the task spec for an agent with file-write and shell access — that
hasn't changed, and is worth remembering if this ever moves to auto-merge.

## Success Criteria

- A ticket placed in `BACKLOG` with a real description and no `meeting-action-item`
  label reaches an open, verified PR with zero human interaction at any stage
- The board visibly reflects progress in real time: `BACKLOG → TO DO → IN PROGRESS →
  IN REVIEW`
- Non-engineering meeting action items never enter the autonomous pipeline at any stage
- A ticket the agent can't complete returns to `TO DO` (not stuck in `IN PROGRESS`),
  is marked `failed` in `dev_agent_runs`, and does not retry indefinitely
- No Anthropic API key or billing involved anywhere in this flow

## Implementation Phases

| Phase | Deliverable |
|-------|-------------|
| 14 | `is_engineering_task` classification — `models.py` + `extractor.py` (amends Phase 2/6) |
| 15 | `jira_client.py` (extracted + new read/search/comment/transition) + `jira_pusher.py` label logic |
| 16 | `dev_agent/` package scaffold — `models.py`, `db.py`, `requirements.txt`, `Dockerfile` |
| 17 | `dev_agent/git_ops.py` (worktrees) + `dev_agent/claude_runner.py` (headless Claude Code) |
| 18 | `dev_agent/github_client.py` (PR verification) + `dev_agent/orchestrator.py` (triage + implement + FastAPI app) |
| 19 | `docker-compose.yml` + `Makefile` + `.env.example` wiring + `CLAUDE.md` updates |
| 20 | `docs/DEV_AGENT.md` + manual single-ticket test path |
