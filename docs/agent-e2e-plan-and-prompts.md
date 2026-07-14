# Agent End-to-End Plan: Jira Ticket → Shipped Feature

**Project:** airbyte-lm-studio-memgraph (v4) · **Phases 28–34** · Prepared 2026-07-14

One Jira ticket = one feature. The agent must **pick up → plan → implement → debug → review → ship → close the loop back into the graph**. Matteo's four points are woven in: review guardrails (Phase 30), meeting quality as priority (Phase 31, parallelizable — start it alongside Phase 28 if Matteo wants it first), a more expressive property graph (Phase 32), and subgraph/hierarchy/org access (Phase 33).

---

## 0. Codebase reconciliation (verified against live code + running graph, 2026-07-14)

This plan was checked against the actual repo and the live Memgraph/Postgres containers before being finalized. Read this before executing any phase.

**Verified TRUE (the plan's understanding of the system is accurate):** 126 tests; 74 Meetings in the graph; `dev_agent` routes headless Claude Code to LM Studio with `ANTHROPIC_API_KEY` emptied (`claude_runner.py`); `jira_client.py` is the sole Jira-REST module and `dev_agent/orchestrator.py` imports it (boundary intact); `uuid5_id` / `strip_json_fences` / `with_retry` exist in `utils.py`; the APScheduler nightly-job pattern and `/webhook/airbyte` exist in `main.py`; the `memgraph-mcp` service and Louvain/Leiden community detection exist.

**Five reconciliations folded into the phases below:**

1. **Lint/type toolchain does not exist** — no `ruff`, `mypy`, `pyproject.toml`, or Makefile targets (only `make test`). Phase 29's DEBUG loop and Phase 30's `lint_type_clean` gate call tools that aren't installed. → New **Phase 28.5** adds the toolchain first.
2. **`agent_runs` collides with the existing `dev_agent_runs` table** (`dev_agent/db.py`, live in Postgres). → Phase 29 **extends** `dev_agent_runs`; it does not create a parallel table.
3. **Env var overlap:** `DEV_AGENT_LM_MODEL` already exists (default `gemma3-12b`); `DEV_AGENT_BACKLOG_STATUS` goes obsolete once triage is sprint-based. → Phase 28 reuses/renames explicitly instead of adding `DEV_AGENT_MODEL`.
4. **Phase 31's inputs barely exist:** the live graph has **0 Decision nodes and 3 ActionItem nodes** across 74 meetings (the Decision/PRODUCED write path in `memgraph_client.py` is real but unexercised). `decision_yield` / `action_yield` / `action_completion` are degenerate until extraction populates them. → Phase 31 gains an explicit data-readiness gate (Task 0).
5. **`raw_calendar_events` is empty; calendar data lives in `raw_gcal_events`** (121 rows). Attendance/role derivation must go through `db.py`'s calendar accessor (already prefers `raw_gcal_events`, falls back to `raw_calendar_events`), never the named raw table directly.

**Doc drift (fix while executing):** CLAUDE.md line 75 still says "6 node / 7 edge types" (actual: 11 node types, ~16 edge types, 13 populated live); CLAUDE.md line 215 lists `MENTIONS` as existing though it is absent from code and graph (Phase 32 adds it). `memgraph/mcp-memgraph:latest` is unpinned.

**Live Jira confirmed (2026-07-14):** the SCRUM workflow has exactly `To Do`, `In Progress`, `In Review`, `Done` — **no `Backlog` status**, so `status in ("Backlog")` structurally never matches (Phase 28's triage premise holds). There is one active sprint, `SCRUM Sprint 0` (id 2), so the sprint-membership rewrite has a live target.

---

## 1. Free LLM strategy (zero cost, verified July 2026)

The dev agent is blocked on LM Studio's 8192 context < Claude Code's tool overhead. Free fix, two tiers:

**Tier 1 — Local (default, privacy story intact):** Reload LM Studio with a coder-tuned model at ≥32k context. On the M2 Pro 16GB, a ~7B coder model (Qwen-family coder, Q4) at 32k context fits where Gemma3:12b at 32k would not — the KV cache is the real budget. Keep Gemma3:12b for extraction (short contexts); load the coder model only for dev-agent runs.

**Tier 2 — Free hosted APIs (heavy tickets, still $0):** Route Claude Code through a LiteLLM proxy container to free tiers — OpenRouter free models (incl. Qwen3 Coder 480B, 262k ctx; ~20 RPM / 50–1000 RPD), Google Gemini Flash free tier (~1,500 req/day, 1M ctx), Groq free tier (Llama 3.3 70B, 30 RPM / 1,000 RPD). Combining providers multiplies free capacity since limits are independent. One ticket/day fits comfortably.

Backend is env-selected (`DEV_AGENT_LLM_BACKEND=local|claude|openrouter|gemini|groq`), **default `local`**. `claude` routes to the real Anthropic API for anyone else using this repo who prefers it; the free tiers keep it $0. This is now a sanctioned, env-gated exception in CLAUDE.md's Absolute Rules — it applies ONLY to the dev agent's coding work. Demo narrative stays clean: *meeting data never leaves the Mac* — only ticket text + repo code go to a hosted model, and only when you opt in.

Sources: [OpenRouter free models](https://openrouter.ai/collections/free-models), [Free LLM APIs 2026 compared](https://openrouter.ai/blog/tutorials/free-llm-apis-compared/), [Free LLM API tiers: Groq, Cerebras, Mistral](https://ianlpaterson.com/blog/free-llm-api-2026/), [Open-source LLMs for agentic coding 2026](https://www.mindstudio.ai/blog/best-open-source-llms-agentic-coding-2026)

---

## 2. Target lifecycle (the spine)

```
Jira ticket (sprint, label=dev-agent)
   │ TRIAGE      sprint-membership JQL (no Backlog status exists), claim → In Progress
   ▼
   PLAN         pull graph context (memory_retrieval: related meetings, decisions,
   │            facts, prior tickets) → design spec → posted as Jira comment
   ▼
   IMPLEMENT    git worktree → headless Claude Code session → small commits
   ▼
   DEBUG        make test + make lint + make typecheck loop, max 3 self-fix attempts
   ▼
   REVIEW       guardrails: deterministic gates + LLM reviewer verdict
   │            fail → structured feedback → back to IMPLEMENT (max 2 cycles)
   │            still failing → label needs-human, comment, stop
   ▼
   SHIP         push branch → GitHub PR (spec-linked description) → In Review
   ▼
   CLOSE LOOP   on merge: transition Done, MERGE (:Ticket)-[:RESOLVED_BY]->(:PullRequest)
                into Memgraph, mark linked ActionItem completed
```

Every stage transition is persisted (the existing Postgres `dev_agent_runs` table, extended in Phase 29) so a crashed run resumes instead of restarting. One ticket at a time (16GB constraint).

**Phase order & dependencies:** 28 → 28.5 → 29 → 30 are sequential (28.5 adds the lint/type toolchain the DEBUG loop and guardrails depend on; the rest are the agent spine). 31 (meeting quality) is independent — run it in parallel or first, per Matteo's priority. 32 → 33 build on each other; 32 also enriches the context the agent gets in PLAN. 34 is the live-verification capstone.

---

## 3. How to run these prompts

- One phase = one fresh Claude Code session in the v4 repo. Start each in **plan mode** (`shift+tab`), approve the plan, then let it execute.
- Prompt 0 (CLAUDE.md) goes first, once — it makes every later prompt shorter and safer.
- After each phase: `make test` must be green (start: 126 passing), then **live-verify** before calling it done. Phase 27's lesson twice over: live runs find bugs unit tests miss (ID-derivation mismatches, SDK shape drift).
- Pin any new dependency version immediately.

---

## Prompt 0 — CLAUDE.md ground rules (run once)

> **Reconciliation note:** the repo already has a detailed ~17KB CLAUDE.md with module boundaries, absolute rules, and env vars. **MERGE** these ground rules into it — do not replace it (a wholesale rewrite would drop the existing module-boundary and absolute-rules sections). While editing, fix the stale schema line ("6 node types · 7 edge types" → 11 node types / ~16 edge types) and remove `MENTIONS` from the current-edge-types list (it does not exist yet — Phase 32 adds it).

```text
Read the entire codebase structure first (transform_service/, dev_agent/, docker-compose,
Makefile, tests/). Then create or update CLAUDE.md at the repo root with these
non-negotiable project rules, phrased as instructions to future agent sessions:

1. Python 3.11+, fully typed, Pydantic v2 models for all structured data.
2. Module boundaries are law:
   - memgraph_client.py is the ONLY file with generic Cypher.
   - db.py is the ONLY file with SQL.
   - jira_client.py is the ONLY file with Jira REST calls.
   - graph_algorithms.py is the ONLY file with MAGE CALL procedures.
   - action_agent.py is the ONLY file using airbyte-agent-sdk.
3. All external calls wrapped in @with_retry(max_attempts=3, base_delay=2.0); HTTP via
   httpx.AsyncClient only.
4. Every deterministic ID via uuid5_id(), re-derived identically wherever the node is
   later referenced (a writer/reader ID mismatch is a known past bug class).
5. All graph writes are MERGE (never CREATE), inside single transactions.
6. All LLM JSON output passes through strip_json_fences() before parsing (local LLMs
   wrap JSON in code fences despite instructions).
7. The v3 repo shubham-gaur-x/airbyte-meeting is READ-ONLY. Never modify it.
8. Never touch .env, credentials, or docker-compose secret values in code changes.
9. make test must pass (currently 126 tests) before any task is considered done; new
   features ship with new tests.
10. Pin exact versions of any new dependency (unpinned SDK shape drift caused a real
    Phase 27 bug).
11. After unit tests pass, the feature must be verified LIVE against real services once
    before being declared working.

Do not change any application code in this session. Deliverable: CLAUDE.md only.
```

---

## Phase 28 — Unblock the dev agent (context + triage + free backend)

**Goal:** `dev_agent` runs live again. Fixes both known blockers and adds the $0 backend router.

```text
Context: dev_agent/ (separate Docker service) takes a Jira ticket → git worktree →
headless Claude Code routed to a local LM Studio endpoint → GitHub PR → In Review.
It is unit-tested but blocked live by two known issues. Read dev_agent/ and CLAUDE.md
fully before planning.

Task 1 — Model preflight + coder model support:
- LM Studio's loaded context (8192) is smaller than Claude Code's tool-definition
  overhead. Add a preflight check that runs before any agent work: query the LM Studio
  server for the loaded model and its context length; if context < 32768 or no model is
  loaded, fail fast with an actionable error message that names the required model and
  context setting.
- Reuse the EXISTING DEV_AGENT_LM_MODEL env var for the coder model (it already exists,
  defaults to gemma3-12b) — change its default to a Qwen-family 7B coder model rather than
  adding a new DEV_AGENT_MODEL var. Add one new var DEV_AGENT_MIN_CONTEXT (default 32768).
  Document in README the exact LM Studio steps: which model to
  download, how to set context length to 32k, and why a 7B coder model (KV-cache memory
  on 16GB) instead of Gemma3:12b. Extraction keeps using Gemma3:12b — do not change
  extractor.py.

Task 2 — Fix triage against the real Jira workflow:
- The current triage queries status in ("Backlog") via jira_client.list_eligible_tickets(),
  called from orchestrator.triage_backlog() with DEV_AGENT_BACKLOG_STATUS (default
  "Backlog"). CONFIRMED against live Jira (2026-07-14): the SCRUM workflow has only To Do /
  In Progress / In Review / Done and no Backlog status, so that JQL never matches. Active
  sprint is "SCRUM Sprint 0" (id 2).
  Rewrite list_eligible_tickets (or add a sprint-scoped sibling) to sprint membership:
  candidates are issues in the active sprint (sprint in openSprints()), status = "To Do",
  label "dev-agent" (only explicitly-labeled tickets are eligible — a deliberate
  guardrail). Sprint data is also available locally in the raw_jira_sprints table if a JQL
  fallback is needed. Retire the now-obsolete DEV_AGENT_BACKLOG_STATUS var. All Jira calls
  stay in jira_client.py.
- On pickup: transition to In Progress, assign to the agent account, and comment
  "Picked up by dev_agent run <run_id>". All Jira calls stay in jira_client.py.

Task 3 — Backend toggle (default local $0; hosted is opt-in):
- POLICY (reconciled with CLAUDE.md): local LM Studio is the DEFAULT and the demo path.
  Hosted backends are a sanctioned, env-gated, opt-in exception for OTHER users of this
  repo, applying ONLY to the dev agent's coding work — meeting-data extraction stays local
  always. CLAUDE.md's Absolute Rules have been amended to permit exactly this; do not
  re-tighten them.
- DEV_AGENT_LLM_BACKEND env var: "local" (default, LM Studio) | "claude" (real Anthropic
  API — requires ANTHROPIC_API_KEY) | "openrouter" | "gemini" | "groq" (free hosted tiers,
  keys optional). Write ONE function that resolves backend → env dict (base URL, auth token,
  model) for the headless Claude Code invocation; unit-test it for all FIVE values,
  including the invariant that "local" empties ANTHROPIC_API_KEY so api.anthropic.com stays
  unreachable.
- Add a litellm proxy service to docker-compose (pinned image version) for the free hosted
  tiers; the "claude" backend routes straight to api.anthropic.com (no proxy needed). All
  hosted keys via env vars, all optional.
- README section "Running the dev agent" covering: the local default, the "claude" switch
  for external users, the free $0 hosted tiers, and the privacy trade-off (any hosted
  backend = ticket text + code leave the Mac; meeting data never does).

Definition of done: make test green with new tests for preflight, triage JQL builder,
and backend resolution; then a LIVE smoke run: preflight passes against a running
LM Studio with 32k context, triage finds a real labeled ticket in the active sprint and
transitions it. Do not run the full implement loop yet.
```

---

## Phase 28.5 — Lint/type toolchain prerequisite (blocks Phases 29–30)

**Goal:** `make lint` and `make typecheck` exist and pass, so Phase 29's DEBUG loop and Phase 30's `lint_type_clean` guardrail have something to call. This phase exists because the repo currently has NO ruff/mypy at all.

```text
Context: the repo has `make test` (126 tests) but no ruff or mypy — no pyproject.toml,
no config, no deps, no Makefile targets. Phase 29's DEBUG loop and Phase 30's
lint_type_clean gate both assume these exist. Add them before those phases. Read the
Makefile and both requirements.txt files first.

Task 1 — Add tooling (pinned): add ruff and mypy at PINNED versions to
transform_service/requirements.txt and dev_agent/requirements.txt. Create pyproject.toml
at repo root with a ruff config (line length, target py311, a sensible default rule set)
and a mypy config (python_version 3.11, ignore_missing_imports for third-party libs that
ship no stubs). Scope both tools to transform_service/ and dev_agent/.

Task 2 — Makefile targets: add `make lint` (ruff check) and `make typecheck`
(mypy transform_service dev_agent), each run inside the transform_service container the
same way `make test` is (docker compose exec -w /app transform_service ...).

Task 3 — Baseline to green: run both and fix or explicitly scope-ignore existing
violations so both pass clean on the current tree. Record any blanket ignores with a
one-line reason. Do NOT change application behavior to satisfy a linter — prefer a scoped
ignore over a risky refactor in this phase.

Definition of done: `make lint` and `make typecheck` both exit 0 on the current tree;
`make test` still green (126); no application logic changed.
```

---

## Phase 29 — Full lifecycle state machine (plan → implement → debug → ship)

**Goal:** one ticket flows through every stage with persisted state and graph-grounded planning.

```text
Context: Phase 28 unblocked dev_agent. Now turn it into an explicit, resumable
lifecycle. Read dev_agent/, transform_service/memory_retrieval.py,
transform_service/db.py, and CLAUDE.md before planning.

Task 1 — State machine + persistence:
- New module dev_agent/lifecycle.py: states TRIAGED → PLANNED → IMPLEMENTING →
  DEBUGGING → REVIEWING → SHIPPED → CLOSED, plus FAILED(reason) and NEEDS_HUMAN.
  Explicit transition table; illegal transitions raise.
- Persist runs by EXTENDING the existing dev_agent/db.py `dev_agent_runs` table (do NOT
  create a parallel agent_runs table — dev_agent_runs already exists and is live). Add
  columns: state (text), state_payload (jsonb); reuse the existing ticket_key,
  attempt_count, started_at, updated_at. Derive run_id as uuid5 from ticket key + attempt.
  All SQL stays in dev_agent/db.py (the dev agent's sanctioned SQL module per CLAUDE.md).
  On startup, resume any non-terminal run instead of picking a new ticket. One active
  run at a time.

Task 2 — PLAN stage (graph-grounded):
- Before writing code, gather context: call the existing entrypoint
  memory_retrieval.full_memory_query(question: str) -> dict, passing the ticket summary +
  description as the question, to fetch related Meetings, Facts, People, Topics, and prior
  ActionItems. (Decisions are currently sparse/absent in the live graph — do not depend on
  them being present.) Assemble a context pack (Pydantic model) from the returned dict.
- Generate a short design spec via the configured LLM backend: problem restatement,
  approach, files expected to change, test plan, out-of-scope. Parse through
  strip_json_fences.
- Post the spec as a Jira comment (via jira_client.py) so a human can veto before code
  exists, and store it in state_payload. Configurable PLAN_APPROVAL_WAIT_MINUTES
  (default 0 for demo; if >0, wait and abort if a human comments "veto").

Task 3 — IMPLEMENT stage:
- Git worktree per run (branch name agent/<ticket-key>). Invoke headless Claude Code
  with the design spec + ticket + context pack as the prompt. Require small, meaningful
  commits (spec section = commit).
- Hard limits in the runner: max wall-clock (env, default 30 min), max diff size
  (default 600 changed lines) — exceeding either → FAILED with reason, worktree
  preserved for inspection.

Task 4 — DEBUG stage:
- Run make test, make lint, make typecheck inside the worktree (the lint/typecheck
  targets come from Phase 28.5 — this stage depends on it). On failure, feed the failing
  output back into a fix-it Claude Code invocation. Max 3 attempts, then NEEDS_HUMAN: label
  the ticket "needs-human", comment with the last failure output, stop cleanly.

Task 5 — SHIP + CLOSE LOOP:
- On green: push branch, open a GitHub PR whose description embeds the design spec,
  ticket link, and test evidence; transition ticket to In Review with the PR link as a
  comment.
- New: dev_agent notifies transform_service via a webhook endpoint (add to main.py)
  when a run reaches SHIPPED; a poller (jira_agent.py pattern) later detects the merge/
  Done transition and, via memgraph_client.py, MERGEs:
  (:Ticket {key})-[:RESOLVED_BY]->(:PullRequest {url, merged_at}) and sets the linked
  ActionItem status=completed. Use uuid5_id for both nodes, derived from ticket key and
  PR url respectively — document the derivation in one place so future readers re-derive
  identically.

Definition of done: unit tests for the transition table, resume logic, context-pack
assembly, and ID derivations; make test green; then ONE live end-to-end run on a small
real ticket (e.g., "add a /version endpoint to transform_service") — capture the Jira
comments, PR, and resulting graph nodes as demo evidence.
```

---

## Phase 30 — Review guardrails (Matteo: "review — bugs have guardrails")

**Goal:** shipped code passes deterministic gates + an independent LLM review; bugs are caught before the PR, and failures escalate to a human instead of looping forever.

```text
Context: Phase 29's lifecycle has a REVIEWING state that is currently a pass-through.
Build the real guardrail layer. Read dev_agent/lifecycle.py and CLAUDE.md first.

Task 1 — Deterministic gates (dev_agent/guardrails.py), each returning a typed
GateResult(name, passed, evidence). All must pass:
  1. tests_green: full make test suite (not just new tests) passes in the worktree.
  2. lint_type_clean: `make lint` (ruff) + `make typecheck` (mypy) clean — both targets
     added in Phase 28.5.
  3. diff_budget: ≤ 600 changed lines, ≤ 10 files (env-configurable).
  4. protected_paths: diff touches nothing under .env*, docker-compose secrets, any
     credentials file, .github/workflows, or any path outside this repo (the v3 repo
     is read-only, always).
  5. no_new_deps: requirements/lockfiles unchanged unless the ticket description
     explicitly contains "allow-new-dependency"; any allowed addition must be pinned.
  6. secret_scan: regex scan of the diff for key/token/password patterns.
  7. module_boundaries: AST-level check that Cypher strings appear only in
     memgraph_client.py, SQL only in db.py, Jira REST calls only in jira_client.py,
     MAGE CALLs only in graph_algorithms.py, and airbyte-agent-sdk imports only in
     action_agent.py. This codifies the existing convention as an enforced gate.

Task 2 — LLM reviewer (independent pass):
- A reviewer invocation (fresh session, no shared context with the implementer —
  reviewer independence is the point) receives: ticket, design spec, full diff, gate
  evidence. It returns strict JSON {verdict: approve|request_changes, findings:
  [{severity, file, issue, suggested_fix}]} — parsed via strip_json_fences, validated
  by Pydantic. Reviewer checks: spec-diff alignment, edge cases, error handling,
  concurrency issues, ID-derivation consistency (known past bug class), test adequacy.

Task 3 — Feedback loop with a ceiling:
- request_changes or any failed gate → structured feedback (findings + failed gate
  evidence) goes back to IMPLEMENT as a fix prompt. Max 2 review cycles. Still
  failing → NEEDS_HUMAN: Jira label "needs-human", comment summarizing exactly which
  gates/findings remain, worktree preserved. The agent must never force-push, never
  bypass a gate, and never merge its own PR — merging stays human.

Task 4 — Audit trail:
- Every gate result and reviewer verdict appended to dev_agent_runs.state_payload and
  posted as a single structured Jira comment per cycle ("Guardrail report, cycle 2/2").
  This is the demo artifact for Matteo's guardrails point.

Definition of done: unit tests per gate (including a deliberately-planted violation for
each — a secret in a diff fixture, a Cypher string in the wrong module, an oversized
diff); an integration test of the full REVIEWING loop with a mocked reviewer; make test
green; then re-run the Phase 29 live ticket flow and capture the guardrail report
comment as evidence.
```

---

## Phase 31 — Meeting quality scoring (Matteo priority — independent, can run first/parallel)

**Goal:** the graph doesn't just remember meetings, it judges them and recommends fixes — "optimize for high quality meetings."

```text
Context: transform_service has 74 real meetings with attendees and topics in Memgraph,
plus a nightly consolidation job pattern (see semantic_memory.py / episodic_memory.py for
the job style). IMPORTANT DATA REALITY: decisions and action items are NEARLY ABSENT in
the live graph (0 Decision nodes, 3 ActionItem nodes across 74 meetings — the Decision/
PRODUCED write path in memgraph_client.py exists but real extraction has not populated it).
So decision_yield, action_yield, and action_completion will be degenerate until that is
fixed; Task 0 below gates on it. Read graph_builder.py, memgraph_client.py, jira_agent.py,
main.py, and CLAUDE.md first.

Task 0 — Data-readiness gate (do this FIRST):
- Query the live graph for how many Meetings have >=1 PRODUCED Decision and >=1
  ActionItem. If the counts are as low as observed (0 / 3), choose and STATE ONE path in
  the module docstring: (a) fix the extraction -> graph_builder path so decisions/action
  items are actually written for the existing 74 meetings before scoring has meaning, or
  (b) treat decision_yield/action_yield/action_completion as "insufficient data" and
  exclude them from the composite (like a null component), scoring on attendance/agenda/
  recurrence only. Do NOT ship quality scores that silently average over empty signals.

Task 1 — meeting_quality.py (new module, follows the memory-module pattern):
Compute per-meeting component scores in [0,1], stored as properties on the Meeting
node via memgraph_client.py (MERGE, single transaction):
  - attendance_ratio: attended vs invited (read via the db.py calendar accessor, which
    prefers raw_gcal_events and falls back to raw_calendar_events — note raw_calendar_events
    is empty; the real data with attendee response info is in raw_gcal_events.attendees_json.
    If unavailable, null, excluded from composite).
  - decision_yield: Decisions PRODUCED per hour of meeting, normalized against the
    distribution across all meetings (percentile, not absolute).
  - action_yield: ActionItems produced per hour, same normalization.
  - action_completion: fraction of this meeting's ActionItems whose Jira status is
    Done (jira_agent.py read path) — measured, not guessed.
  - agenda_present: calendar description contains agenda-like structure (rules-based,
    classifier.py style scoring; no LLM).
  - recurrence_health: for recurring series (match by normalized title + attendee
    overlap), trend of the above over the last 5 occurrences — a decaying series
    scores low.
Composite quality_score = weighted mean of available components (weights in one
config dict, documented). Also set quality_components as a map property and
quality_computed_at.

Task 2 — Scheduling:
- Fast path: score a meeting right after graph_builder finishes writing it.
- Nightly job (APScheduler, alongside the existing consolidation jobs): rescore all
  meetings from the last 90 days (action_completion changes as Jira moves) and
  recompute recurrence_health.

Task 3 — Recommendations + surfacing:
- Nightly, emit MeetingRecommendation findings for the worst offenders: recurring
  series with quality_score < 0.4 for 3+ occurrences → "shorten / merge / make async /
  needs agenda", each with the evidence numbers. Store as a
  (:Meeting)-[:HAS_RECOMMENDATION]->(:Recommendation) node (MERGE by uuid5 of series +
  recommendation type) so Claude Desktop can query them via MCP.
- Extend memory_retrieval.py so natural-language questions like "which recurring
  meetings are lowest quality?" and "which meetings produce decisions?" resolve to
  these properties.
- Add GET /meetings/quality (main.py) returning ranked series with components, for
  demo screenshots.

Definition of done: unit tests for every component scorer with synthetic fixtures
(including missing-data cases), the composite weighting, and recurrence matching;
make test green; then live-verify by running the nightly job against the real 74
meetings and sanity-checking the top/bottom 5 ranked series by hand. Capture the
/meetings/quality output for the deck.
```

---

## Phase 32 — More expressive property graph (Matteo)

**Goal:** richer semantics on nodes/edges so both humans and agents get sharper context. This directly improves the dev agent's PLAN stage and Phase 31's inputs.

```text
Context: current schema is 11 node types and ~16 edge types (13 populated in the live
graph). NOTE the doc drift: CLAUDE.md line 75 is stale ("6 node / 7 edge") and line 215
lists MENTIONS as existing though it is NOT in code or graph — this phase actually adds it.
The Decision node + PRODUCED edge write path exists in memgraph_client.py but is currently
unexercised (0 Decision nodes live), so Decision-status/SUPERSEDES work here is building on
a path that must first produce Decisions. All writes MERGE-only via memgraph_client.py;
extraction happens in extractor.py (LM Studio + Gemma3:12b). Read extractor.py, graph_builder.py,
memgraph_client.py, and the memory modules first.

Task 1 — Schema extensions (additive only, no breaking renames):
  New node types: Ticket {key, summary, status, url}, PullRequest {url, merged_at},
  Team {name}, Project {key, name}. (Ticket/PullRequest formalize what Phase 29
  writes; if Phase 29 isn't merged yet, define them here and Phase 29 reuses.)
  New/enriched edges:
   - ATTENDED gains {role: organizer|required|optional, attended: bool} — invited
     no-shows become visible (calendar response data via db.py raw tables).
   - DISCUSSED gains {weight: int} (mention count within the meeting).
   - Decision gains {status: proposed|accepted|superseded}; new edge
     SUPERSEDES{decided_at} between Decisions.
   - ActionItem: new edges DEPENDS_ON and BLOCKS between ActionItems.
   - MENTIONS: Meeting → Ticket/Project when a ticket key or project appears in the
     meeting text (regex, not LLM).
   - Provenance on every extracted node: {source_id, extracted_at,
     extractor_version, confidence}.

Task 2 — Extractor upgrade:
- Extend the extraction prompt + Pydantic response models to emit the new fields
  (attendee roles, decision status, action-item dependencies, mention spans). Keep the
  JSON schema small enough for Gemma3:12b — measure prompt+response tokens and stay
  well inside the loaded context. strip_json_fences everywhere. Fields the model can't
  reliably produce are omitted (validated optional), never hallucinated defaults.

Task 3 — Idempotent migration + backfill:
- scripts/migrate_schema_v5.py: MERGE-only, safe to re-run, adds properties/edges to
  existing data where derivable without the LLM (roles from calendar data, MENTIONS
  from regex over stored meeting text, provenance defaults). Log a summary count per
  change type. No re-extraction of the 74 meetings in this phase.

Task 4 — Keep consumers coherent:
- Update memory_retrieval.py and the MCP-facing schema description so NL queries can
  use the new semantics ("who was invited but didn't attend?", "which decisions were
  superseded?", "which meetings mention SCRUM-47?"). Update the schema doc/README.

Definition of done: unit tests for new extraction models, migration idempotency
(run twice, identical graph), and the MENTIONS regex; make test green; live-verify by
running the migration on the real graph, then extracting ONE new sample email
end-to-end and inspecting the enriched nodes in Memgraph Lab. Capture before/after
schema screenshots.
```

---

## Phase 33 — Subgraphs, hierarchy, and org-level access (Matteo)

**Goal:** answer "how do you organize a subgraph, handle hierarchy, org-level details and access" with working code, not slideware.

```text
Context: the graph currently has a flat Organization/Person structure and a single
implicit scope — everything is visible to every consumer (Claude Desktop MCP, agents,
API). Read memgraph_client.py, memory_retrieval.py, the MCP server setup (the memgraph-mcp
docker-compose service, image memgraph/mcp-memgraph:latest — pin it while you are here),
and Phase 32's Team/Project nodes first.

Design stance (state this in the design doc you produce first):
- HIERARCHY is modeled IN the graph: (:Person)-[:MEMBER_OF]->(:Team)-[:PART_OF]->
  (:Organization); (:Project)-[:OWNED_BY]->(:Team); (:Meeting)-[:BELONGS_TO]->(:Team
  or :Project). A subgraph is not a separate database — it is the traversal closure of
  a scope anchor (an Org, Team, or Project node).
- ACCESS is enforced at the retrieval boundary (app layer), because Memgraph Community
  lacks fine-grained ACLs; label-based access control is a Memgraph Enterprise feature
  and is the documented production path. All reads already flow through
  memory_retrieval.py / the MCP server, so the choke point exists.

Task 1 — Hierarchy inference + scope stamping:
- Infer Team membership from the data we have: co-attendance communities
  (community_detection.get() for the fast path / igraphalg.community_leiden() for the
  nightly path in graph_algorithms.py already SET node.community_id — reuse those results)
  + email domains + Jira project membership. Auto-created Teams get {inferred: true} and can be renamed/confirmed
  via a small PUT endpoint.
- graph_builder.py stamps every newly-written node with scope_org and scope_team
  properties at write time (derived from the meeting's attendees/project). Migration
  script backfills existing nodes. Stamping is denormalized on purpose: scope checks
  become a property filter, not a per-query traversal.

Task 2 — Scoped retrieval:
- memory_retrieval.py and the MCP query surface gain a required scope parameter
  (org | team:<name> | project:<key> | all). Every generated Cypher gets the scope
  filter injected in ONE shared function in memgraph_client.py — never hand-added
  per query (a missed filter is an access bug).
- Aggregate queries (PageRank, communities) run per-scope where meaningful.

Task 3 — Principal → scope policy:
- access_control.py: a static policy map (YAML/env) from principal (MCP client name,
  agent name, API key) → allowed scopes, with role levels: member (own team),
  lead (team + org-level rollups but not other teams' details), admin (all). The MCP
  server resolves the caller to a principal and passes only allowed scopes downward;
  a scope the principal lacks raises a typed AccessDenied that the MCP layer turns
  into a polite refusal.
- Org-level detail handling: org-scope queries return AGGREGATES (counts, trends,
  top-N with names only from your own team) unless the principal is admin — this is
  the concrete answer to "org level details and access".

Task 4 — Demo story:
- A demo script that runs the same NL question ("what are the biggest open action
  items?") as three principals (member / lead / admin) and shows three different
  answers. This is the Matteo-facing artifact.

Definition of done: unit tests for scope injection (including the negative case:
query WITHOUT scope raises), policy resolution, and AccessDenied paths; migration
idempotency test; make test green; live-verify the three-principal demo against the
real graph and capture the outputs. Document the Enterprise-LBAC production path in
README.
```

---

## Phase 34 — Live end-to-end capstone + demo assets

```text
Context: Phases 28–33 are merged; make test is green. This session writes no features —
it proves the system and produces evidence.

1. Create a real, small Jira ticket in the active sprint labeled "dev-agent"
   (e.g., "Add GET /stats endpoint: meeting count, node/edge counts, avg quality
   score"). Note it touches main.py + memgraph_client.py — exercising module
   boundaries and the new quality data.
2. Run the full lifecycle live and record at each stage: triage pickup comment, design
   spec comment, commits, a guardrail report (ideally one failed cycle — if all gates
   pass first try, plant a review finding to demonstrate the feedback loop on a
   scratch branch), the PR, In Review transition, and after human merge: the Done
   transition plus the (:Ticket)-[:RESOLVED_BY]->(:PullRequest) subgraph in Memgraph
   Lab.
3. Run the Phase 33 three-principal demo and the /meetings/quality ranking; screenshot
   both.
4. Log every discrepancy between expected and actual behavior as a bug list; fix only
   trivial ones now, file the rest as labeled Jira tickets (which become the agent's
   own future backlog — the system now feeds itself).
5. Update docs/demo_assets: add a "v5 Agent Lifecycle" section to the overview deck
   outline (bullets + which screenshot goes where) so the deck update is a separate,
   quick pptx pass.

Definition of done: one ticket went from sprint to merged PR to closed loop in the
graph with zero manual code edits, and every claim in the deck has a screenshot behind
it.
```

---

## 4. Answers you can give Matteo directly

**Review — bugs have guardrails:** seven deterministic gates (tests, lint/type, diff budget, protected paths, pinned deps, secret scan, enforced module boundaries) plus an independent LLM reviewer with a structured verdict; bounded retry (2 cycles) then human escalation; full audit trail on the ticket. The agent never merges its own PR.

**High quality meetings:** measured, not vibes — six component scores per meeting including *actual* action-item completion from Jira, recurrence health trends, nightly rescoring, and concrete recommendations (shorten/merge/async/needs-agenda) queryable in natural language.

**More expressive graph:** attendee roles and no-shows, decision lifecycle (proposed/accepted/superseded), action-item dependencies, ticket/PR nodes closing the loop from meeting → code, and provenance on every extracted fact.

**Subgraph / hierarchy / org access:** hierarchy lives in the graph (Person→Team→Org, Project→Team); a subgraph is the traversal closure of a scope anchor, made cheap by scope-stamping at write time; access is enforced at the single retrieval choke point with member/lead/admin roles — org-level queries return aggregates unless you're admin; Memgraph Enterprise LBAC is the documented production hardening path.
