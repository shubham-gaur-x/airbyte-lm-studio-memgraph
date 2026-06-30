"""Dev agent orchestrator — triage, implement, FastAPI app."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dev_agent import db, git_ops, github_client, claude_runner
from dev_agent.models import ClaudeRunResult
from transform_service import jira_client

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Config from env (all optional with sane defaults so tests can import freely)
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


JIRA_PROJECT_KEY = lambda: _env("JIRA_PROJECT_KEY", "SCRUM")
DEV_AGENT_BACKLOG_STATUS = lambda: _env("DEV_AGENT_BACKLOG_STATUS", "Backlog")
DEV_AGENT_TODO_STATUS = lambda: _env("DEV_AGENT_TODO_STATUS", "To Do")
DEV_AGENT_IN_PROGRESS_STATUS = lambda: _env("DEV_AGENT_IN_PROGRESS_STATUS", "In Progress")
DEV_AGENT_REVIEW_STATUS = lambda: _env("DEV_AGENT_REVIEW_STATUS", "In Review")
DEV_AGENT_SKIP_LABELS = lambda: [l.strip() for l in _env("DEV_AGENT_SKIP_LABELS", "meeting-action-item").split(",") if l.strip()]
DEV_AGENT_POLL_MINUTES = lambda: int(_env("DEV_AGENT_POLL_MINUTES", "10"))
DEV_AGENT_BATCH_SIZE = lambda: int(_env("DEV_AGENT_BATCH_SIZE", "5"))
DEV_AGENT_MAX_TURNS = lambda: int(_env("DEV_AGENT_MAX_TURNS", "40"))
DEV_AGENT_TIMEOUT_SECONDS = lambda: int(_env("DEV_AGENT_TIMEOUT_SECONDS", "1800"))
DEV_AGENT_MAX_ATTEMPTS = lambda: int(_env("DEV_AGENT_MAX_ATTEMPTS", "1"))
DEV_AGENT_LM_MODEL = lambda: _env("DEV_AGENT_LM_MODEL") or None
GITHUB_OWNER = lambda: _env("GITHUB_OWNER")
GITHUB_REPO = lambda: _env("GITHUB_REPO")
GITHUB_TOKEN = lambda: _env("GITHUB_TOKEN")
REPO_DIR = lambda: _env("DEV_AGENT_REPO_DIR", "/work/repo")
WORK_ROOT = lambda: _env("DEV_AGENT_WORK_ROOT", "/work/worktrees")


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(ticket: Dict[str, Any]) -> str:
    key = ticket["key"]
    summary = ticket.get("summary", "")
    description = ticket.get("description", "")
    return f"""Read CLAUDE.md and follow all conventions in this repository.

Implement the following Jira ticket in full:

Ticket: {key}
Summary: {summary}
Description:
{description}

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

async def triage_backlog() -> Dict[str, Any]:
    candidates = await jira_client.list_eligible_tickets(
        JIRA_PROJECT_KEY(),
        [DEV_AGENT_BACKLOG_STATUS()],
        DEV_AGENT_SKIP_LABELS(),
        require_description=True,
    )
    promoted = 0
    skipped = 0
    for ticket in candidates:
        ok = await jira_client.transition_issue(ticket["key"], DEV_AGENT_TODO_STATUS())
        if ok:
            promoted += 1
            log.info("orchestrator.triage.promoted", key=ticket["key"])
        else:
            skipped += 1

    log.info(
        "orchestrator.triage.done",
        considered=len(candidates),
        promoted=promoted,
        skipped=skipped,
    )
    return {"considered": len(candidates), "promoted": promoted, "skipped": skipped}


# ---------------------------------------------------------------------------
# Implement a single ticket
# ---------------------------------------------------------------------------

async def process_ticket(ticket: Dict[str, Any]) -> None:
    key = ticket["key"]
    bound_log = log.bind(ticket_key=key)
    branch_name = f"agent/{key}"

    await db.start_run(key, branch_name)

    try:
        ok = await jira_client.transition_issue(key, DEV_AGENT_IN_PROGRESS_STATUS())
        if not ok:
            bound_log.warning("orchestrator.in_progress_transition_failed", key=key)

        detail = await jira_client.get_issue_detail(key)
        work_dir = f"{WORK_ROOT()}/{key}"

        await git_ops.create_worktree(REPO_DIR(), work_dir, branch_name)
        prompt = build_prompt(detail)

        result: ClaudeRunResult = await claude_runner.run_claude_code(
            work_dir,
            prompt,
            timeout_seconds=DEV_AGENT_TIMEOUT_SECONDS(),
            max_turns=DEV_AGENT_MAX_TURNS(),
            model=DEV_AGENT_LM_MODEL(),
        )

        if not result.success:
            bound_log.error("orchestrator.claude_failed", error=result.result_text[:200])
            await db.finish_run(key, "failed", error=result.result_text[:2000])
            await jira_client.add_comment(
                key,
                "Dev agent could not complete this ticket automatically (see dev_agent logs). Needs human follow-up.",
            )
            await jira_client.transition_issue(key, DEV_AGENT_TODO_STATUS())
            return

        pr = await github_client.find_open_pr(GITHUB_OWNER(), GITHUB_REPO(), branch_name)
        if pr is None:
            error_msg = "claude_code reported success but no PR was found for this branch"
            bound_log.error("orchestrator.pr_not_found")
            await db.finish_run(key, "failed", error=error_msg)
            await jira_client.add_comment(key, f"Dev agent reported success but no PR was found. Needs human follow-up.")
            await jira_client.transition_issue(key, DEV_AGENT_TODO_STATUS())
            return

        await jira_client.add_comment(key, f"Implemented automatically. PR: {pr['html_url']}")
        ok = await jira_client.transition_issue(key, DEV_AGENT_REVIEW_STATUS())
        if not ok:
            bound_log.warning("orchestrator.review_transition_failed", key=key)

        await db.finish_run(key, "pr_opened", pr_url=pr["html_url"], pr_number=pr["number"])
        bound_log.info("orchestrator.ticket_done", pr_url=pr["html_url"])

    except Exception as exc:
        bound_log.error("orchestrator.unexpected_error", exc_info=True)
        try:
            await db.finish_run(key, "failed", error=str(exc))
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

    await git_ops.ensure_repo_cloned(REPO_DIR(), GITHUB_OWNER(), GITHUB_REPO(), GITHUB_TOKEN())
    await triage_backlog()

    tickets = await jira_client.list_eligible_tickets(
        JIRA_PROJECT_KEY(),
        [DEV_AGENT_TODO_STATUS()],
        DEV_AGENT_SKIP_LABELS(),
    )

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
    result = await triage_backlog()
    return result


@app.get("/runs")
async def list_runs():
    runs = await db.list_recent_runs()
    return {"runs": [r.model_dump() for r in runs]}
