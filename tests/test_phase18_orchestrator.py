"""Phase 18: Tests for orchestrator.process_ticket and triage_backlog."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stubs for deps not installed locally
# ---------------------------------------------------------------------------

for mod_name in ("structlog", "asyncpg", "httpx", "apscheduler",
                 "apscheduler.schedulers", "apscheduler.schedulers.asyncio"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        if mod_name == "structlog":
            _log = MagicMock()
            _log.bind = MagicMock(return_value=_log)
            stub.get_logger = lambda: _log  # type: ignore[attr-defined]
        if mod_name == "apscheduler.schedulers.asyncio":
            stub.AsyncIOScheduler = MagicMock  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

for mod_name in ("neo4j", "neo4j.exceptions"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        stub.AsyncGraphDatabase = MagicMock()  # type: ignore[attr-defined]
        stub.AsyncDriver = MagicMock()  # type: ignore[attr-defined]
        stub.ServiceUnavailable = Exception  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

if "openai" not in sys.modules:
    stub = types.ModuleType("openai")
    stub.AsyncOpenAI = MagicMock  # type: ignore[attr-defined]
    stub.APIConnectionError = Exception  # type: ignore[attr-defined]
    sys.modules["openai"] = stub

# fastapi stubs
if "fastapi" not in sys.modules:
    stub = types.ModuleType("fastapi")
    stub.FastAPI = MagicMock  # type: ignore[attr-defined]
    stub.APIRouter = MagicMock  # type: ignore[attr-defined]
    stub.Depends = MagicMock  # type: ignore[attr-defined]
    sub = types.ModuleType("fastapi.middleware")
    sub.cors = types.ModuleType("fastapi.middleware.cors")
    sub.cors.CORSMiddleware = MagicMock  # type: ignore[attr-defined]
    sys.modules["fastapi"] = stub
    sys.modules["fastapi.middleware"] = sub
    sys.modules["fastapi.middleware.cors"] = sub.cors

from dev_agent.models import ClaudeRunResult, DevAgentRun


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TICKET = {
    "key": "SCRUM-42",
    "summary": "Add health check endpoint",
    "description": "Add a /healthz endpoint that returns 200 OK.",
    "status": "To Do",
    "labels": [],
    "priority": "Medium",
}

SAMPLE_PR = {"number": 7, "html_url": "https://github.com/owner/repo/pull/7"}


# ---------------------------------------------------------------------------
# process_ticket — success path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_ticket_success():
    """Happy path: Claude succeeds, PR found, ticket moves to IN REVIEW."""
    import dev_agent.orchestrator as orch

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.jira_client, "transition_issue", AsyncMock(return_value=True)),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=True, returncode=0, result_text="done", num_turns=5)
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=SAMPLE_PR)),
        patch.dict("os.environ", {
            "GITHUB_OWNER": "owner", "GITHUB_REPO": "repo",
            "JIRA_PROJECT_KEY": "SCRUM",
        }),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    mock_finish.assert_called_once_with(
        "SCRUM-42", "pr_opened",
        pr_url=SAMPLE_PR["html_url"],
        pr_number=SAMPLE_PR["number"],
    )


# ---------------------------------------------------------------------------
# process_ticket — claude_runner failure
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_ticket_claude_failure():
    """Claude returns success=False → ticket back to TO DO, status=failed."""
    import dev_agent.orchestrator as orch

    transition_calls: list = []

    async def _transition(key, status):
        transition_calls.append(status)
        return True

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.jira_client, "transition_issue", side_effect=_transition),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=False, returncode=1, result_text="error output")
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock()),
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    mock_finish.assert_called_once()
    call_kwargs = mock_finish.call_args
    assert call_kwargs[0][1] == "failed"  # status
    # Ticket must have been transitioned back toward TO DO
    assert any("To Do" in s for s in transition_calls)


# ---------------------------------------------------------------------------
# process_ticket — PR not found after claimed success
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_ticket_no_pr_after_success():
    """Claude claims success but no PR found → treated as failure, back to TO DO."""
    import dev_agent.orchestrator as orch

    transition_calls: list = []

    async def _transition(key, status):
        transition_calls.append(status)
        return True

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.jira_client, "transition_issue", side_effect=_transition),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=True, returncode=0, result_text="PR_URL: none")
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=None)),  # no PR!
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    call_kwargs = mock_finish.call_args
    assert call_kwargs[0][1] == "failed"
    assert any("To Do" in s for s in transition_calls)


# ---------------------------------------------------------------------------
# process_ticket — exception during git/jira
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_ticket_exception_during_execution():
    """Unexpected exception in the body → failed, transition back to TO DO, no re-raise."""
    import dev_agent.orchestrator as orch

    transition_calls: list = []

    async def _transition(key, status):
        transition_calls.append(status)
        return True

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.jira_client, "transition_issue", side_effect=_transition),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(side_effect=RuntimeError("Jira down"))),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        # Must NOT raise
        await orch.process_ticket(SAMPLE_TICKET)

    call_kwargs = mock_finish.call_args
    assert call_kwargs[0][1] == "failed"
    assert any("To Do" in s for s in transition_calls)


# ---------------------------------------------------------------------------
# triage_backlog — promotion logic
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_triage_backlog_promotes_eligible_tickets():
    import dev_agent.orchestrator as orch

    candidates = [
        {"key": "SCRUM-10", "summary": "Fix bug", "status": "Backlog", "labels": []},
        {"key": "SCRUM-11", "summary": "Add feature", "status": "Backlog", "labels": []},
    ]

    with (
        patch.object(orch.jira_client, "list_eligible_tickets", AsyncMock(return_value=candidates)),
        patch.object(orch.jira_client, "transition_issue", AsyncMock(return_value=True)) as mock_transition,
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        result = await orch.triage_backlog()

    assert result["promoted"] == 2
    assert mock_transition.call_count == 2


@pytest.mark.anyio
async def test_triage_backlog_skips_when_transition_fails():
    import dev_agent.orchestrator as orch

    candidates = [{"key": "SCRUM-20", "summary": "Something", "status": "Backlog", "labels": []}]

    with (
        patch.object(orch.jira_client, "list_eligible_tickets", AsyncMock(return_value=candidates)),
        patch.object(orch.jira_client, "transition_issue", AsyncMock(return_value=False)),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        result = await orch.triage_backlog()

    assert result["promoted"] == 0
    assert result["skipped"] == 1
