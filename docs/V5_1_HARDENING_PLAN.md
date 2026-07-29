# v5.1 — Completion & Hardening Plan

Status: **A, B1–B5, C1–C3, D, E all done and live-validated.** 363 tests green (from 316 at the
start of this plan). Only genuinely-external actions remain — see "What's actually left" below.

Legend: 🟩 code (implementable now) · 🟦 ops/config (needs your creds/action) · 🟨 dashboard (deferred UI)

---

## Phase A — Activate & validate what's already built (no new code) 🟦 **DONE**

The live containers still run pre-merge code (`transform_service` up ~2 weeks, `dev_agent` up ~10h).

- **A1** Redeploy: `docker compose up -d --force-recreate transform_service dev_agent`. Constraints +
  `raw_meet_transcripts` are already applied; this only reloads code.
- **A2** Health-check all services + `/preflight` on the dev agent.
- **A3** Live smoke: seed one meeting → confirm ticket creation, per-item confidence on the ActionItem,
  provenance nodes, and (if a low-confidence item) the review-queue path.

Exit criteria: new endpoints (`/webhook/github`) respond; a seeded meeting flows through the new gates.

**Result:** redeployed; all services healthy. A3 surfaced a real bug — `gemma3-12b` sometimes emits
the literal string `"null"` for a field instead of a real JSON null, which `if not data.get(...)`
doesn't catch (a non-empty string is truthy) — fixed with `_is_null_like`, applied to every
extractor fallback (platform/date/summary/owner/task/confidence). Also fixed a real, unrelated
test-isolation bug this surfaced later (see B3): `test_env_anthropic_api_key_cleared` silently
depended on ambient `DEV_AGENT_LLM_BACKEND` being unset.

---

## Phase B — Complete the dormant / partial code 🟩 (test-first, one commit each) **DONE**

### B1 — Wire the `lifecycle.py` state machine into `orchestrator.process_ticket`
Phase 29 built a full state machine (`TRIAGED→PLANNED→IMPLEMENTING→…→CLOSED`, escalations, deterministic
IDs) that `process_ticket` never uses. Wire it so a crashed run resumes instead of restarting:
- Call `db.set_state`/`assert_transition` at each stage; persist stage artifacts in `state_payload`.
- On startup, `db.get_active_run()` resumes the single non-terminal run.
- Compose with P7: the resume already reads `state_payload["memory"]`; add the state cursor next to it.
- Tests: `test_phase46_lifecycle_wiring.py` (legal transitions per stage, resume from each state).
  **Landed as designed.** Live-confirmed in Phase E: SCRUM-53's run correctly reached `state=SHIPPED`.

### B2 — Finish P4: per-item confidence on Decision and Fact
Today only `ActionItem.confidence` gates. Extend:
- Promote `ExtractedMeeting.decisions` from `List[str]` to `List[Decision]` (`Decision{text, confidence}`);
  update `extractor` prompt + `upsert_meeting_graph` (write `d.confidence`).
- Gate low-confidence Decisions/Facts into `needs_review` (mirror `mark_action_needs_review`).
- Tests: `test_phase47_decision_fact_confidence.py`.
- ⚠️ Migration note: `decisions` shape change touches `memgraph_client` + any reader; keep backward-compat
  (accept both `str` and `{text,confidence}` in the validator). **Done via a `field_validator`.**
- **Design change from the original plan:** Fact already had real confidence dynamics
  (`semantic_memory`: seeded 0.3, +0.1/repeat mention), so its gate ended up read-time
  (`FACT_MIN_CONFIDENCE` floor in `person_memory_profile`), not a write-time `needs_review` — a Fact
  has no Jira-ticket-style side effect to block.

### B3 — Review-queue surfacing endpoints
P3/P4/P9 write review artifacts nothing exposes. Add read endpoints (Cypher in `memgraph_client`):
- `GET /review/actions` — ActionItems with `jira_status = needs_review` + their confidence.
- `GET /review/people` — `PersonReview` nodes with their meeting.
- `GET /review/blockers` — open `Blocker` nodes + the ticket that raised them.
- Tests: `test_phase48_review_queues.py`.
- **Bonus find:** this test was the first to actually import the real `transform_service.main`
  in the shared pytest session, surfacing a systemic bug — several test files stub optional-looking
  deps (fastapi, structlog, openai, neo4j, apscheduler) behind `if mod_name not in sys.modules`,
  written for an environment without the real package; all ARE installed here, but the guard meant
  whichever test ran first "won" for the whole session. Fixed by generalizing the pre-existing
  `_REAL_HTTPX` pattern in `conftest.py` to every module this touches.

### B4 — Single-traversal provenance endpoint (dashboard data layer)
- `GET /graph/provenance/{meeting_id}` (and `/by-ticket/{key}`) returning
  `meeting → decision → action item → ticket → AgentRun → PR → files` in one Cypher `MATCH`.
- This is the query the dashboard consumes; building it now proves the graph shape end-to-end.
- Tests: `test_phase49_provenance_traversal.py`.
- **Live-proven in Phase E**, with one real fix along the way: `get_ticket_provenance` (the reverse
  direction) was missing `c.message` in its `RETURN` — the forward direction had it, the reverse
  didn't, so `commit.message` was always `null` on that path. Fixed for parity.

### B5 — Transcript + Meet-pull scheduler
`process_new_transcripts` / `meet_ingest.pull_and_stage` only run on the Airbyte webhook. Add an
APScheduler job (mirror the existing poll) so transcripts drain on an interval and via Pub/Sub pull.
- Tests: `test_phase50_transcript_scheduler.py`.

---

## Phase C — Live external wiring 🟦 (needs your creds/action; thin code from me) — **runbooks done, wiring itself pending you**

### C1 — P1 Google Meet capture (the biggest external dependency)
- GCP project + a **Pub/Sub PULL** subscription; a Workspace Events subscription for
  `google.workspace.meet.transcript.v2.fileGenerated`; OAuth token with Meet + Pub/Sub scopes.
- Set `GOOGLE_ACCESS_TOKEN`, `MEET_PUBSUB_SUBSCRIPTION`. Then `meet_ingest.pull_and_stage` goes live.
- I provide: a setup runbook + a token-refresh helper; you provide: the GCP project + consent.

### C2 — P3 canonical roster
- Either a static `roster.json` (name + primary email + aliases + `tracked`) or a Google Workspace
  Directory sync script. Set `PERSON_ROSTER_PATH`. I can build the sync script; you grant Directory scope.

### C3 — GitHub webhook registration
- Register a repo webhook → `/webhook/github` (via the existing `bore` tunnel or a public URL) for
  `pull_request`, `push`, `check_suite`; set `GITHUB_WEBHOOK_SECRET`. Then merges flow back into the graph.
- I provide: the `gh api ... /hooks` command + secret wiring; you approve exposing the endpoint.

**Status:** all three runbooks + helper scripts written — see
`docs/V5_1_EXTERNAL_WIRING_RUNBOOKS.md`, `scripts/refresh_meet_token.py`,
`sample_data/roster.example.json`. The actual live wiring (GCP console steps, exposing a public
URL, registering the webhook) is real external action only you can take — nothing here was done
unilaterally, per the same judgment call as the original v5 plan.

---

## Phase D — Provenance dashboard 🟨 **DONE**

Consumes B4. Read-only, **aggregate-first** per the governance decision (no per-person leaderboards
unless `tracked`). Timeline (day/week/month) + a meeting→PR provenance view + review-queue panels (B3).
Kept self-contained (single-page, no external hosting, no build step — vanilla JS).

**Live-verified in-browser** (not just unit-tested): all four tabs — Timeline, Review Queue,
Provenance Lookup, Insights — render real graph data with zero console errors. Screenshotted during
Phase E against the live SCRUM-53 chain.

---

## Phase E — End-to-end validation & hardening 🟩🟦 **DONE**

Ran a real (not fixture) live E2E: seeded a genuine meeting → P6 type-routed ("planning") → P4
confidence-gated (1.0, passed) → real Jira ticket **SCRUM-53** created → triggered the dev agent
(`claude-haiku-4-5`, cost-controlled) → **B1** lifecycle correctly reached `SHIPPED` despite the
underlying Claude Code call itself reporting `error_max_turns` (the exact SCRUM-50 failure mode,
confirming that fix generalizes) → real PR **[#3](https://github.com/shubham-gaur-x/airbyte-lm-studio-memgraph/pull/3)**
opened with a genuine `/version` endpoint + tests → **P8** self-verify scored it `confidence=0.95,
passed=true` → **P2** provenance written → **P7** session memory recorded → Jira moved to `In Review`
with an accurate comment. Simulated the P2 GitHub push webhook with real commit/file data from the
actual PR (since C3's live registration is still pending your action) — `Commit` + 3 `FileChange`
nodes landed. `GET /graph/provenance/by-ticket/SCRUM-53` and the dashboard's Provenance Lookup tab
both render the complete `meeting → ticket → agent run → PR → commit → files` chain in one query,
exactly the v5 target end-state.

**Three real bugs found and fixed live** (each with a regression test, none caught by the unit suite
beforehand):
1. Extractor `"null"`-string fallback bug (Phase A, above).
2. `/webhook/github`'s `log.info(..., event=event)` collided with structlog's own reserved `event`
   kwarg → real `TypeError` → HTTP 500. Every existing test called `handle_event()` directly, never
   the actual route function, so a real `structlog` call was never exercised. Fixed + added an
   `httpx.ASGITransport` test that drives the real ASGI app end-to-end.
3. `get_ticket_provenance` missing `c.message` (B4, above).

**Left deliberately undone, on purpose:** PR #3 was not merged (the human checkpoint), SCRUM-53 was
not closed — both left for your morning review, exactly as the "one remaining human checkpoint" rule
requires.

Suite: 316 → **363** tests, all green. `/graphify . --update` re-run after this work.

---

## What's actually left

Only Phase C's live external wiring — GCP console setup for Meet capture, filling in a real roster,
and registering the GitHub webhook publicly. All three are genuine external actions (credentials,
public exposure) that only you can authorize; the runbooks in
`docs/V5_1_EXTERNAL_WIRING_RUNBOOKS.md` have the exact steps. Everything else in this plan is done,
committed, and live-validated.
