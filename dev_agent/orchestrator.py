"""Dev agent orchestrator — triage, implement, FastAPI app."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dev_agent import backend, db, git_ops, github_client, claude_runner, lifecycle as lc, self_verify, session_memory
from dev_agent.backend import PreflightError
from dev_agent.models import ClaudeRunResult
from transform_service import jira_client, memgraph_client

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config from env (all optional with sane defaults so tests can import freely)
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


JIRA_PROJECT_KEY = lambda: _env("JIRA_PROJECT_KEY", "SCRUM")
DEV_AGENT_TODO_STATUS = lambda: _env("DEV_AGENT_TODO_STATUS", "To Do")
DEV_AGENT_IN_PROGRESS_STATUS = lambda: _env("DEV_AGENT_IN_PROGRESS_STATUS", "In Progress")
DEV_AGENT_REVIEW_STATUS = lambda: _env("DEV_AGENT_REVIEW_STATUS", "In Review")
DEV_AGENT_SKIP_LABELS = lambda: [lbl.strip() for lbl in _env("DEV_AGENT_SKIP_LABELS", "meeting-action-item").split(",") if lbl.strip()]
DEV_AGENT_POLL_MINUTES = lambda: int(_env("DEV_AGENT_POLL_MINUTES", "10"))
DEV_AGENT_BATCH_SIZE = lambda: int(_env("DEV_AGENT_BATCH_SIZE", "5"))
DEV_AGENT_MAX_TURNS = lambda: int(_env("DEV_AGENT_MAX_TURNS", "40"))
DEV_AGENT_TIMEOUT_SECONDS = lambda: int(_env("DEV_AGENT_TIMEOUT_SECONDS", "1800"))
DEV_AGENT_MAX_ATTEMPTS = lambda: int(_env("DEV_AGENT_MAX_ATTEMPTS", "1"))
DEV_AGENT_LM_MODEL = lambda: _env("DEV_AGENT_LM_MODEL") or None
DEV_AGENT_LLM_BACKEND = lambda: _env("DEV_AGENT_LLM_BACKEND", "local")
DEV_AGENT_MIN_CONTEXT = lambda: int(_env("DEV_AGENT_MIN_CONTEXT", "32768"))
DEV_AGENT_REQUIRE_LABELS = lambda: [lbl.strip() for lbl in _env("DEV_AGENT_REQUIRE_LABELS", "dev-agent").split(",") if lbl.strip()]
GITHUB_OWNER = lambda: _env("GITHUB_OWNER")
GITHUB_REPO = lambda: _env("GITHUB_REPO")
GITHUB_TOKEN = lambda: _env("GITHUB_TOKEN")
REPO_DIR = lambda: _env("DEV_AGENT_REPO_DIR", "/work/repo")
WORK_ROOT = lambda: _env("DEV_AGENT_WORK_ROOT", "/work/worktrees")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(ticket: Dict[str, Any], resume_context: Optional[str] = None) -> str:
    key = ticket["key"]
    summary = ticket.get("summary", "")
    description = ticket.get("description", "")
    resume_block = (
        f"\nResume context from a previous attempt (use it, do not start over):\n{resume_context}\n"
        if resume_context else ""
    )
    return f"""Read CLAUDE.md and follow all conventions in this repository.

Implement the following Jira ticket in full:

Ticket: {key}
Summary: {summary}
Description:
{description}
{resume_block}
Instructions:
- Implement the ticket completely.
- Run the test suite (pytest / make test if pytest isn't available directly) and confirm it passes before finishing.
- Do NOT modify .env files, secrets, or anything outside the repository working directory.
- Do NOT merge or attempt to merge any PR yourself.
- After implementation is complete and tests pass, commit and push:
    git add -A
    git commit -m "[{key}] {summary[:60]}"
    git push -u origin {f"agent/{key}"}
- Then open a PR:
    gh pr create --title "[{key}] {summary[:80]}" --body "Implements {key}: {summary}. See ticket for full description." --base main --head agent/{key}
- On the very last line of your output, print the PR URL exactly like this:
    PR_URL: <url>
"""


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

async def find_sprint_candidates() -> list[Dict[str, Any]]:
    """Eligible tickets: in the active sprint, status To Do, labelled for the agent.

    There is no Backlog status in the workflow (verified live), so triage is
    sprint-membership based. Only tickets a human has put in the sprint AND
    labelled ``dev-agent`` are eligible — a deliberate guardrail.

    P4: a second, independent confidence gate. If the ticket traces to an extracted
    ActionItem whose confidence is below DEV_AGENT_CONFIDENCE_THRESHOLD, it is held back
    from autonomous coding even though it is labelled — we do not rely on the label alone.
    A ticket with no linked ActionItem (e.g. human-authored) passes this gate.
    """
    candidates = await jira_client.list_active_sprint_tickets(
        JIRA_PROJECT_KEY(),
        [DEV_AGENT_TODO_STATUS()],
        DEV_AGENT_REQUIRE_LABELS(),
        DEV_AGENT_SKIP_LABELS(),
    )
    threshold = float(_env("DEV_AGENT_CONFIDENCE_THRESHOLD", "0.6"))
    eligible = []
    for ticket in candidates:
        conf = await memgraph_client.get_action_confidence(ticket["key"])
        if conf is not None and conf < threshold:
            log.info("orchestrator.triage.low_confidence_skip", key=ticket["key"], confidence=round(conf, 2))
            continue
        eligible.append(ticket)
    return eligible


async def triage() -> Dict[str, Any]:
    """Report the eligible sprint candidates (no state change — the poll claims them)."""
    candidates = await find_sprint_candidates()
    log.info("orchestrator.triage.done", eligible=len(candidates),
             keys=[c["key"] for c in candidates])
    return {"eligible": len(candidates), "keys": [c["key"] for c in candidates]}


# ---------------------------------------------------------------------------
# Implement a single ticket
# ---------------------------------------------------------------------------

async def _advance_state(key: str, new_state: str) -> None:
    """Validate and persist a lifecycle transition (B1 — wires dev_agent/lifecycle.py in).

    Reads the current state and checks the edge is legal, per lifecycle.py's own intent
    ("illegal transitions raise, turning a logic bug into a loud failure instead of silent
    corruption") — but a sequencing bug here must not crash ticket processing, so we log
    and still persist rather than raise. `current is None` (a fresh run, or a run that
    predates state tracking) skips validation and just writes the first state.
    """
    run = await db.get_run(key)
    current = run.state if run else None
    if current and current != new_state and not lc.can_transition(current, new_state):
        log.warning(
            "orchestrator.illegal_state_transition",
            ticket_key=key, from_state=current, to_state=new_state,
        )
    await db.set_state(key, new_state)


async def process_ticket(ticket: Dict[str, Any]) -> None:
    key = ticket["key"]
    bound_log = log.bind(ticket_key=key)
    branch_name = f"agent/{key}"

    await db.start_run(key, branch_name)
    # B1: TRIAGED — the run is claimed. The single claude_runner call below covers what
    # the state table models as separate PLANNED/IMPLEMENTING/DEBUGGING phases (there is
    # no per-phase Claude Code invocation today), so we pass through them as checkpoints
    # around it rather than skipping straight to IMPLEMENTING (which the table forbids).
    await _advance_state(key, lc.TRIAGED)

    try:
        ok = await jira_client.transition_issue(key, DEV_AGENT_IN_PROGRESS_STATUS())
        if not ok:
            bound_log.warning("orchestrator.in_progress_transition_failed", key=key)
        await jira_client.add_comment(key, f"Picked up by dev_agent (backend={DEV_AGENT_LLM_BACKEND()}).")

        detail = await jira_client.get_issue_detail(key)
        await _advance_state(key, lc.PLANNED)
        work_dir = f"{WORK_ROOT()}/{key}"

        await git_ops.create_worktree(REPO_DIR(), work_dir, branch_name)
        # P7: on a retry, feed the prior attempt's resume context instead of starting cold.
        resume_context = await session_memory.load_resume_context(key)
        prompt = build_prompt(detail, resume_context=resume_context)

        await _advance_state(key, lc.IMPLEMENTING)
        result: ClaudeRunResult = await claude_runner.run_claude_code(
            work_dir,
            prompt,
            timeout_seconds=DEV_AGENT_TIMEOUT_SECONDS(),
            max_turns=DEV_AGENT_MAX_TURNS(),
            model=backend.model_for_run(DEV_AGENT_LLM_BACKEND()),
        )
        # DEBUGGING: the agent's attempt is over; we are now checking the outcome.
        await _advance_state(key, lc.DEBUGGING)

        # Check for a PR *regardless* of the success flag. A run can push a branch and
        # open a PR and then still report failure (e.g. it hits the turn limit on the
        # verification step afterwards — the live SCRUM-50 failure mode). Dropping that
        # PR and reverting the ticket to TO DO loses good work, so the PR check gates the
        # outcome, not `result.success`.
        pr = await github_client.find_open_pr(GITHUB_OWNER(), GITHUB_REPO(), branch_name)

        if pr is None:
            # No PR produced — a genuine failure whichever way the run reported.
            reason = (result.result_text or "").strip()[:500] or "no error detail captured"
            if result.success:
                bound_log.error("orchestrator.pr_not_found")
                await db.finish_run(key, "failed", error="reported success but no PR was found")
                await jira_client.add_comment(
                    key, "Dev agent reported success but no PR was found. Needs human follow-up."
                )
            else:
                bound_log.error("orchestrator.claude_failed", error=result.result_text[:200])
                await db.finish_run(key, "failed", error=result.result_text[:2000])
                await jira_client.add_comment(
                    key,
                    "Dev agent could not complete this ticket automatically. Needs human "
                    f"follow-up.\n\nError: {reason}",
                )
            # P7: record a resumable session memory even on failure (no PR) so a retry
            # continues from here instead of cold.
            await session_memory.record(
                detail, outcome="failed", error=reason, raw_notes=result.result_text or "",
            )
            # P9: surface the blocker as a lightweight graph node (best-effort).
            try:
                await memgraph_client.merge_blocker(reason, ticket_key=key)
            except Exception:
                bound_log.warning("orchestrator.blocker_write_failed", exc_info=True)
            await _advance_state(key, lc.FAILED)
            await jira_client.transition_issue(key, DEV_AGENT_TODO_STATUS())
            return

        await _advance_state(key, lc.REVIEWING)
        # A PR exists. Self-verify the diff against the ticket intent (P8) — a cheap scoring
        # pass through the SAME backend. It never blocks review: a low score only flags the
        # comment and sets AgentRun.verified=false. Non-fatal.
        verdict = None
        diff = ""
        try:
            diff = await github_client.get_pr_diff(GITHUB_OWNER(), GITHUB_REPO(), pr["number"])
            verdict = await self_verify.verify_pr(
                ticket, diff, model=backend.model_for_run(DEV_AGENT_LLM_BACKEND())
            )
        except Exception:
            bound_log.warning("orchestrator.self_verify_failed", exc_info=True)
        verified = verdict.passed if (verdict and verdict.checked) else None

        # Record provenance at the same point we record the dev_agent_runs row, so the run
        # is reachable in one traversal (P2). Non-fatal: a graph hiccup must not lose the
        # PR link or block the Jira transition.
        run = await db.get_run(key)
        attempt = run.attempt_count if run and run.attempt_count else 1
        try:
            await memgraph_client.write_run_provenance(
                ticket_key=key, attempt=attempt, pr_url=pr["html_url"],
                pr_number=pr.get("number"), branch=branch_name,
                ticket_summary=ticket.get("summary", ""), status="pr_opened",
                verified=verified,
            )
        except Exception:
            bound_log.warning("orchestrator.provenance_write_failed", exc_info=True)

        # Compose the Jira comment: did the run finish + did the automated check confirm it.
        base = (
            "Implemented automatically." if result.success
            else "PR opened, but the agent's run ended early (e.g. turn limit) before finishing."
        )
        if verdict and verdict.checked and not verdict.passed:
            flag = (
                " Automated check could NOT confirm the diff addresses the ticket "
                f"(confidence {verdict.confidence:.2f}: {verdict.reason}) — review carefully."
            )
        elif verdict and verdict.passed:
            flag = " Automated check: the diff appears to address the ticket."
        else:
            flag = ""
        await jira_client.add_comment(key, f"{base}{flag} PR: {pr['html_url']}")

        ok = await jira_client.transition_issue(key, DEV_AGENT_REVIEW_STATUS())
        if not ok:
            bound_log.warning("orchestrator.review_transition_failed", key=key)

        await db.finish_run(key, "pr_opened", pr_url=pr["html_url"], pr_number=pr["number"])
        # SHIPPED: PR opened and Jira moved to review. CLOSED is reserved for an actual
        # merge (github_webhook's pull_request/merged event) — cross-service (dev_agent's
        # own DB from transform_service) and left for a follow-up, not this phase.
        await _advance_state(key, lc.SHIPPED)
        # P7: record the session memory (files changed pulled from the PR diff).
        await session_memory.record(
            detail, outcome="pr_opened", pr=pr,
            files_changed=session_memory.files_from_diff(diff),
            verdict=verdict, raw_notes=result.result_text or "",
        )
        bound_log.info(
            "orchestrator.ticket_done", pr_url=pr["html_url"],
            run_success=result.success, verified=verified,
        )

    except Exception as exc:
        bound_log.error("orchestrator.unexpected_error", exc_info=True)
        try:
            await db.finish_run(key, "failed", error=str(exc))
        except Exception:
            pass
        try:
            await _advance_state(key, lc.FAILED)
        except Exception:
            pass
        try:
            await session_memory.record(ticket, outcome="failed", error=str(exc))
        except Exception:
            pass
        try:
            await jira_client.transition_issue(key, DEV_AGENT_TODO_STATUS())
        except Exception:
            pass
    finally:
        work_dir = f"{WORK_ROOT()}/{key}"
        await git_ops.remove_worktree(REPO_DIR(), work_dir, branch_name, ignore_errors=True)


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------

async def poll_and_process() -> None:
    log.info("orchestrator.poll.start")

    # Preflight the selected LLM backend before touching Jira/git. A misconfigured
    # or unloaded LM Studio fails fast here with an actionable message instead of
    # burning a ticket on a doomed run.
    try:
        detail = await backend.preflight(DEV_AGENT_LLM_BACKEND(), DEV_AGENT_MIN_CONTEXT())
        log.info("orchestrator.poll.preflight_ok", backend=DEV_AGENT_LLM_BACKEND(), detail=detail)
    except PreflightError as exc:
        log.error("orchestrator.poll.preflight_failed", error=str(exc))
        log.info("orchestrator.poll.done", attempted=0, reason="preflight_failed")
        return

    try:
        await git_ops.ensure_repo_cloned(REPO_DIR(), GITHUB_OWNER(), GITHUB_REPO(), GITHUB_TOKEN())
    except Exception as exc:
        log.error("orchestrator.poll.repo_unavailable", error=str(exc))
        log.info("orchestrator.poll.done", attempted=0, reason="repo_unavailable")
        return

    # B1: resume a crashed run before looking at new candidates. A run whose process died
    # mid-flight (e.g. the container was killed during claude_runner) leaves its
    # dev_agent_runs row stuck at status='running' forever — db.should_attempt refuses to
    # ever retry a 'running' ticket, so without this it silently stalls until a human
    # notices. get_active_run() finds that stuck non-terminal state; resuming through
    # process_ticket() re-feeds the P7 resume_context instead of starting cold.
    active = await db.get_active_run()
    if active is not None:
        log.info(
            "orchestrator.poll.resuming_crashed_run",
            ticket_key=active.ticket_key, state=active.state,
        )
        try:
            active_detail = await jira_client.get_issue_detail(active.ticket_key)
            await process_ticket(active_detail)
        except Exception:
            log.error("orchestrator.poll.resume_failed", ticket_key=active.ticket_key, exc_info=True)

    tickets = await find_sprint_candidates()

    eligible = []
    for ticket in tickets:
        if await db.should_attempt(ticket["key"], DEV_AGENT_MAX_ATTEMPTS()):
            eligible.append(ticket)

    batch = eligible[: DEV_AGENT_BATCH_SIZE()]
    skipped = len(eligible) - len(batch)

    log.info(
        "orchestrator.poll.batch",
        considered=len(tickets),
        eligible=len(eligible),
        attempting=len(batch),
        deferred=skipped,
    )

    for ticket in batch:
        await process_ticket(ticket)

    log.info("orchestrator.poll.done", attempted=len(batch))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.ensure_table()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_and_process,
        "interval",
        minutes=DEV_AGENT_POLL_MINUTES(),
        id="poll_and_process",
    )
    scheduler.start()
    log.info("orchestrator.started", poll_minutes=DEV_AGENT_POLL_MINUTES())

    yield

    scheduler.shutdown()


app = FastAPI(title="Dev Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/trigger/{ticket_key}")
async def trigger_ticket(ticket_key: str):
    detail = await jira_client.get_issue_detail(ticket_key)
    await process_ticket(detail)
    run = await db.get_run(ticket_key)
    return {"ticket_key": ticket_key, "run": run.model_dump() if run else None}


@app.post("/triage")
async def trigger_triage():
    result = await triage()
    return result


@app.get("/preflight")
async def check_preflight():
    """Report whether the selected LLM backend is ready to run."""
    backend_name = DEV_AGENT_LLM_BACKEND()
    try:
        detail = await backend.preflight(backend_name, DEV_AGENT_MIN_CONTEXT())
        return {"backend": backend_name, "ready": True, "detail": detail}
    except PreflightError as exc:
        return {"backend": backend_name, "ready": False, "detail": str(exc)}


@app.get("/runs")
async def list_runs():
    runs = await db.list_recent_runs()
    return {"runs": [r.model_dump() for r in runs]}
