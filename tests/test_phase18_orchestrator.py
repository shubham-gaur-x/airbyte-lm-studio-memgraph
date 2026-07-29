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

# NOTE: fastapi is a real installed dependency in this environment (unlike the packages
# stubbed above, which historically were not) — do NOT stub it here. A stub module left in
# sys.modules for the rest of the pytest session shadows the real package for every test
# file that imports transform_service.main afterwards (e.g. `from fastapi import
# BackgroundTasks` starts raising ImportError, since the stub never defined it).

from dev_agent.models import ClaudeRunResult, DevAgentRun
from dev_agent.self_verify import VerifyVerdict

_PASS_VERDICT = VerifyVerdict(checked=True, addresses=True, confidence=0.9, reason="ok")
_UNCHECKED_VERDICT = VerifyVerdict(checked=False, reason="scorer unavailable")


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

_SAMPLE_RUN = DevAgentRun(ticket_key="SCRUM-42", status="running", attempt_count=1)


# ---------------------------------------------------------------------------
# process_ticket — success path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_ticket_success():
    """Happy path: Claude succeeds, PR found, ticket moves to IN REVIEW."""
    import dev_agent.orchestrator as orch

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "set_state", AsyncMock()),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
        patch.object(orch.db, "get_run", AsyncMock(return_value=_SAMPLE_RUN)),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.memgraph_client, "write_run_provenance", AsyncMock()) as mock_prov,
        patch.object(orch.jira_client, "transition_issue", AsyncMock(return_value=True)),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=True, returncode=0, result_text="done", num_turns=5)
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=SAMPLE_PR)),
        patch.object(orch.github_client, "get_pr_diff", AsyncMock(return_value="diff --git a/x b/x")),
        patch.object(orch.self_verify, "verify_pr", AsyncMock(return_value=_PASS_VERDICT)) as mock_verify,
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
    # Provenance recorded at the same point as the run row (P2), with the verify verdict (P8).
    mock_prov.assert_awaited_once()
    assert mock_prov.await_args.kwargs["pr_url"] == SAMPLE_PR["html_url"]
    assert mock_prov.await_args.kwargs["verified"] is True
    mock_verify.assert_awaited_once()


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
        patch.object(orch.db, "set_state", AsyncMock()),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
        patch.object(orch.db, "get_run", AsyncMock(return_value=_SAMPLE_RUN)),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.memgraph_client, "write_run_provenance", AsyncMock()) as mock_prov,
        patch.object(orch.jira_client, "transition_issue", side_effect=_transition),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=False, returncode=1, result_text="error output")
        )),
        # Genuine failure: the run produced no PR.
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    mock_finish.assert_called_once()
    call_kwargs = mock_finish.call_args
    assert call_kwargs[0][1] == "failed"  # status
    # Ticket must have been transitioned back toward TO DO
    assert any("To Do" in s for s in transition_calls)
    # No PR → no provenance written.
    mock_prov.assert_not_awaited()


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
        patch.object(orch.db, "set_state", AsyncMock()),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
        patch.object(orch.db, "get_run", AsyncMock(return_value=_SAMPLE_RUN)),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.memgraph_client, "write_run_provenance", AsyncMock()) as mock_prov,
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
    mock_prov.assert_not_awaited()


# ---------------------------------------------------------------------------
# process_ticket — PR opened but run ended early (the live SCRUM-50 failure mode)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_ticket_pr_exists_despite_run_failure():
    """success=False but a PR was pushed before the turn limit → In Review, not TO DO.

    Regression for the live SCRUM-50 run: Claude opened a PR and *then* hit max_turns on
    the verify step. The old code saw success=False, never checked for the PR, and reverted
    the ticket to TO DO with a 'needs human' comment. The PR must now be honoured.
    """
    import dev_agent.orchestrator as orch

    transitions: list = []

    async def _transition(key, status):
        transitions.append(status)
        return True

    comments: list = []

    async def _comment(key, body):
        comments.append(body)

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "set_state", AsyncMock()),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
        patch.object(orch.db, "get_run", AsyncMock(return_value=_SAMPLE_RUN)),
        patch.object(orch.db, "finish_run", AsyncMock()) as mock_finish,
        patch.object(orch.memgraph_client, "write_run_provenance", AsyncMock()) as mock_prov,
        patch.object(orch.jira_client, "transition_issue", side_effect=_transition),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", side_effect=_comment),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=False, returncode=1, result_text="error_max_turns")
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=SAMPLE_PR)),
        patch.object(orch.github_client, "get_pr_diff", AsyncMock(return_value="diff")),
        patch.object(orch.self_verify, "verify_pr", AsyncMock(return_value=_UNCHECKED_VERDICT)),
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    # Outcome is pr_opened, NOT failed — the PR is not dropped.
    mock_finish.assert_called_once_with(
        "SCRUM-42", "pr_opened", pr_url=SAMPLE_PR["html_url"], pr_number=SAMPLE_PR["number"],
    )
    # Moves to In Review, never back to To Do.
    assert any("In Review" in s for s in transitions)
    assert not any("To Do" in s for s in transitions)
    # Provenance still recorded.
    mock_prov.assert_awaited_once()
    # The comment is flagged distinctly (not the plain "Implemented automatically").
    assert any("ended early" in c and SAMPLE_PR["html_url"] in c for c in comments)


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
        patch.object(orch.db, "set_state", AsyncMock()),
        patch.object(orch.db, "get_run", AsyncMock(return_value=_SAMPLE_RUN)),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
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
# triage — sprint-membership candidate discovery (no Backlog status exists)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_triage_reports_sprint_candidates():
    import dev_agent.orchestrator as orch

    candidates = [
        {"key": "SCRUM-10", "summary": "Fix bug", "status": "To Do", "labels": ["dev-agent"]},
        {"key": "SCRUM-11", "summary": "Add feature", "status": "To Do", "labels": ["dev-agent"]},
    ]

    with (
        patch.object(orch.jira_client, "list_active_sprint_tickets", AsyncMock(return_value=candidates)) as mock_list,
        patch.object(orch.memgraph_client, "get_action_confidence", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        result = await orch.triage()

    # Triage discovers eligible tickets; it must NOT transition them (the poll claims them).
    assert result["eligible"] == 2
    assert result["keys"] == ["SCRUM-10", "SCRUM-11"]
    mock_list.assert_awaited_once()


@pytest.mark.anyio
async def test_triage_empty_when_no_eligible_tickets():
    import dev_agent.orchestrator as orch

    with (
        patch.object(orch.jira_client, "list_active_sprint_tickets", AsyncMock(return_value=[])),
        patch.object(orch.memgraph_client, "get_action_confidence", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        result = await orch.triage()

    assert result["eligible"] == 0
    assert result["keys"] == []
