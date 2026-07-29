# V5 Upgrade Plan — airbyte-lm-studio-memgraph

Status: **planned, not yet implemented** (Phase 0 + Phase 1 complete; awaiting build go-ahead).
This document captures what the v5 upgrade will build so the knowledge graph reflects
both the current codebase and the intended target state. Every phase below refers to
real modules that exist today and names the new modules to be created.

Decisions locked with the project owner:
- **P1 transcript delivery** = Pub/Sub **pull** subscription (no inbound tunnel; preserves the
  fully-local, no-tunnels principle; GCP project + OAuth creds still required for live capture).
- **Per-person analytics governance** = aggregate-only by default; PageRank/centrality stay gated
  behind `Person.tracked` (default false); no per-person leaderboards exposed without human sign-off.
- **Existing scaffolding** = build on the dormant `dev_agent/lifecycle.py`, `memgraph_client.merge_ticket_resolved_by_pr`,
  `memgraph_client.migrate_schema_v5`, and the `dev_agent_runs.state`/`state_payload` columns — wire and extend, do not replace.

---

## Current State (verified Phase 0 findings)

- **Transcripts are never ingested.** Postgres holds only `raw_emails`, `raw_calendar_events`,
  `raw_jira_issues`. `RawCalendarEvent` has `description`, no transcript field. `extract_meeting`
  runs on email `subject+body` or calendar `title+description` — never spoken meeting content.
- **`confidence` is a dead field.** Written onto the Meeting node in `upsert_meeting_graph`, logged
  in `extractor`, never read or gated on.
- **No cross-database provenance (live).** `dev_agent_runs` keyed by `ticket_key` in Postgres;
  `ActionItem.jira_key` in Memgraph; no live join; no GitHub webhooks. Dormant primitives exist
  (`lifecycle.py` node-id derivations, `merge_ticket_resolved_by_pr`, `migrate_schema_v5` constraints,
  `(:Meeting)-[:MENTIONS]->(:Ticket)` edges) but are not wired into `orchestrator.process_ticket`.
- **Entity resolution absent.** `upsert_meeting_graph` does `if not attendee.email: continue`
  (silently drops no-email attendees); `Person` keyed on raw `attendee.email` so any variant duplicates.
- **No dedup.** `jira_pusher.push_action_items` creates a new Jira issue for every action item every run.
- **One extraction schema for all meeting types.** `ExtractedMeeting.kind` is output-only; no routing.
- **dev_agent keeps almost no session memory.** Success posts a one-line PR comment; failure truncates
  to 500 chars; retries start cold. `state_payload` JSONB column exists but `process_ticket` never populates it.
- **Extractor retry gap.** JSON parse failures are caught internally and return `None`, so `@with_retry`
  never retries them (only LM connection errors retry).
- **`jira_agent.sync_jira_issue` always returns `True`**, even when `update_action_jira_status` no-ops on zero matches.

---

## P1 — Real transcript capture (primary new input)

New modules: `transform_service/meet_transcript_source.py`, `transform_service/transcript_ingest.py`.
Touches: `db.py` (new `raw_meet_transcripts` table), `graph_builder.py` (new `process_transcript` path),
`models.py` (new `RawMeetTranscript` model).

- Add Google Meet transcripts as a first-class ingestion source, independent of Airbyte (Airbyte has no
  Meet-transcript connector).
- Subscribe to `google.workspace.meet.transcript.v2.fileGenerated` via the Google Workspace Events API,
  delivered through Cloud **Pub/Sub pull** (dev container polls a pull subscription — no inbound tunnel).
- Fetch text from the Meet REST API `conferenceRecords.transcripts.entries`.
- Land the transcript as a new raw source; `graph_builder` processes it the same way it processes
  emails/events. Transcript becomes the **primary** input to `extract_meeting()`; calendar description
  is demoted to fallback context only.
- `TranscriptSource` is a clean interface so a different capture source (e.g. a notetaker) swaps in
  without touching downstream code.

## P6 — Meeting-type-aware extraction (can overlap P1)

New module: `transform_service/meeting_type_router.py`. Touches: `extractor.py`, `models.py`.

- Insert a type-routing step between `classifier.classify()` (kept as the cheap "is this worth
  processing" gate) and `extract_meeting()`.
- Classify meeting type, then extract with a schema/prompt suited to that type. The type list is derived
  from real meeting titles in the live graph, not a generic list (standup / planning / decision / 1:1 /
  review as starting candidates, validated against data).
- Different meeting types produce structurally different action items.

## Ontology alignment (Matteo's engagement ontology — `~/Desktop/ontology`)

Reviewed Matteo's Obsidian/Dataview engagement ontology. Different substrate (markdown vault
vs. our Memgraph runtime graph), but the type/predicate vocabulary overlaps our v5 work and we
adopt it where it maps cleanly, so our graph is legible to anyone who knows his ontology:
- His **DevLog** ("engineering session where implementation happened") → our **AgentRun** bridge node.
- His predicates `implements` (DevLog→Feature), `follows_up_on` (DevLog→Meeting), `results_in`
  (Meeting→DevLog), `raises_blocker` / `resolves` (Blocker) → our provenance + P9 edge names.
- His **AgentMemory** section shape (Quick Reference + Confidence keywords, Decisions, Work
  Completed, Files Changed, Blockers/Risks, Next Actions, Resume Context, Raw Session Notes) is
  the canonical shape for P7 — conform exactly, plus his sub-categories (Session / Topic /
  Stakeholder Profile memory).
- **Not** adopted: the markdown-vault substrate itself, and the commercial layer
  (Account / Opportunity / Strategy / Competitor / Role) — that's his client-engagement CRM,
  out of scope for the meeting-memory pipeline.

## P2 — Provenance chain + GitHub webhooks (precedes the dashboard; cannot be backfilled)

New module: `transform_service/github_webhook.py`. Touches: `main.py` (new `/webhook/github` receiver
mirroring `/webhook/airbyte`), `memgraph_client.py`, `dev_agent/orchestrator.py`. Builds on `lifecycle.py`
node-id derivations and `merge_ticket_resolved_by_pr`.

**Status (Phase 34, landed):** `memgraph_client.write_run_provenance` writes the `AgentRun` bridge
node + Matteo-aligned edges (`TICKETED_AS`, `IMPLEMENTS`, `PRODUCED`, `FOLLOWS_UP_ON`) in one ACID
transaction; `AgentRun` uniqueness constraint added to `migrate_schema_v5`; `orchestrator.process_ticket`
writes provenance at the same point it records `dev_agent_runs`. Also fixed the live SCRUM-50 failure
mode: the PR check now gates the outcome instead of `result.success`, so a PR opened just before the
turn limit is no longer dropped and reverted to TO DO. **Still to do:** the `/webhook/github` receiver
(`Commit`/`FileChange` nodes on push/pull_request/check_suite).

- New Memgraph node types: `Repository`, `PullRequest`, `Commit`, `FileChange`
  (extends the existing `Ticket`/`PullRequest` from `migrate_schema_v5`).
- New edges: `(ActionItem)-[:TICKETED_AS]->(JiraIssue)`, `(JiraIssue)-[:IMPLEMENTED_BY]->(PullRequest)`,
  `(PullRequest)-[:CONTAINS]->(Commit)`, `(Commit)-[:MODIFIES]->(FileChange)`,
  `(AgentRun)-[:PRODUCED]->(PullRequest)`.
- GitHub webhook receiver handles `pull_request`, `push`, `check_suite`.
- Join key is the `agent/<KEY>` branch name `dev_agent` already produces.
- `orchestrator.process_ticket` writes these graph nodes at the same point it writes `dev_agent_runs`.

## P3 — Entity resolution (every downstream graph number depends on it)

New module: `transform_service/person_resolver.py`. Touches: `memgraph_client.py`, `graph_builder.py`.

- Resolve extracted attendee name+email against a canonical roster before `upsert_meeting_graph` writes.
- Deterministic tier: match against a synced Google Workspace Directory roster (primary email + aliases).
- Probabilistic tier: fuzzy-match names missing the deterministic tier against existing `Person` nodes;
  below a confidence threshold, route to a review queue instead of auto-creating a node.
- No-email case handled explicitly (hold for review, never silently drop).
- Add `Person.tracked: bool = false` (opt-in gate); all per-person analytics respect it (governance decision).

## P4 — Confidence gating (turn the dead field into a real gate)

Touches: `models.py`, `jira_pusher.py`, `memgraph_client.py`, `dev_agent/orchestrator.py`.

- Move `confidence` to per-item (on `ActionItem`, `Decision`, `Fact`), not just per-meeting.
- In `jira_pusher.push_action_items`, gate ticket creation: below threshold, write the node with
  `status: needs_review` and surface it in a review queue instead of creating a Jira issue.
- Add a second, independent confidence check in `orchestrator.find_sprint_candidates` before a ticket is
  picked up for autonomous coding — do not rely solely on the `dev-agent` / `meeting-action-item` label.

## P5 — Dedup (recurring meetings must not spawn duplicate tickets)

Touches: `vector_memory.py` (extend embeddings to `ActionItem`), `jira_pusher.py`, `memgraph_client.py`.

- Before creating a Jira issue, query existing open `ActionItem` nodes with high text/embedding similarity,
  same owner, same topic.
- Above threshold: add a `MENTIONED_IN` edge from the existing item to the new meeting and post a Jira
  comment ("also raised in <meeting>") instead of opening a new issue.
- Below threshold: proceed as today.

## P7 — dev_agent session memory (modeled on the AgentMemory ontology)

New module: `dev_agent/session_memory.py`. Touches: `dev_agent/orchestrator.py`, `dev_agent/github_client.py`.

- At the end of every `process_ticket` run (success or failure), write a structured record:
  Quick Reference + confidence keywords, Decisions, Work Completed, Files Changed (from the PR diff via
  `github_client`), Blockers/Risks, Next Actions, Resume Context, Raw Session Notes (full, not truncated).
- Fold the fields onto the `AgentRun` node from P2 (1:1 relationship; single-hop resume query; keeps
  provenance and memory co-located for the one-traversal end state).
- On retry (`should_attempt` allows a second attempt), `process_ticket` queries the prior
  `AgentRun.resume_context` and feeds it to Claude Code instead of starting cold.

## P8 — Self-verification (check the work, not just that a PR exists) — depends on P2, P7

New module: `dev_agent/self_verify.py`. Touches: `dev_agent/orchestrator.py`, `dev_agent/github_client.py`.

- After `dev_agent` opens a PR and before transitioning to `In Review`, fetch the PR diff and score whether
  it plausibly satisfies the original ticket intent (which traces back to the transcript).
- Below threshold: still transition to `In Review` (do not block the human), but flag the Jira comment
  distinctly ("PR opened, automated check could not confirm it addresses the ticket").
- Depends on P2's `PullRequest` / `FileChange` nodes existing.

**Status (Phase 35, landed):** `dev_agent/self_verify.py` scores the PR diff (via `github_client.get_pr_diff`
+ `claude_runner.run_oneshot` through the dev-agent backend) and returns a verdict; `orchestrator.process_ticket`
sets `AgentRun.verified` and flags the Jira comment accordingly, never blocking In Review. P2 webhook
(Phase 36) landed too: `/webhook/github` → `github_webhook.handle_event` → `merge_ticket_resolved_by_pr`
(merge) / `write_commits_and_files` (push), joined on the `agent/<KEY>` branch.

## P9 — Lightweight Blocker node + cleanup

Touches: `memgraph_client.py`, `extractor.py`, `jira_agent.py`.

- Add `Blocker` as a lightweight node created inline wherever `dev_agent` or `action_agent` first
  references one — no dedicated extraction pipeline.
- Fix `extractor.py` retry semantics: decide explicitly which failures retry (JSON parse failures currently
  return `None` and never retry).
- Fix `jira_agent.sync_jira_issue` so matched/unmatched counters mean something instead of always returning `True`.

---

## Target end-to-end path (one graph traversal)

A Google Meet transcript is captured on `fileGenerated` → routed by meeting type → extracted with per-item
confidence → attendees resolved to canonical people → deduped against open items → confidence-gated into a
Jira ticket or a review queue → engineering tickets picked up by `dev_agent`, which keeps resume-able session
memory, opens a PR, and self-verifies against the original intent → a GitHub webhook syncs merge/commit/file
data back into the graph → `MATCH (m:Meeting)...` returns meeting → decision → action item → ticket → PR →
files changed in one query. Human merge checkpoint intact throughout.
