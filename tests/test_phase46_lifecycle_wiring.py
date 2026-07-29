"""Phase 46 (B1): wire dev_agent/lifecycle.py's state machine into the orchestrator.

The state machine (TRIAGED->PLANNED->IMPLEMENTING->DEBUGGING->REVIEWING->SHIPPED->CLOSED,
FAILED/NEEDS_HUMAN escalations) existed since Phase 29 but process_ticket never called it.
Concretely this closes a real bug: a run that crashes mid-flight (process killed during
claude_runner) leaves dev_agent_runs.status='running' forever, and db.should_attempt
permanently refuses to retry a 'running' ticket. With state tracked, poll_and_process can
detect the stuck non-terminal run via db.get_active_run() and resume it (feeding the P7
resume_context) instead of leaving it stuck until a human intervenes.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dev_agent import lifecycle as lc
from dev_agent.models import ClaudeRunResult, DevAgentRun

SAMPLE_TICKET = {
    "key": "SCRUM-42", "summary": "Add health check endpoint",
    "description": "Add a /healthz endpoint that returns 200 OK.",
    "status": "To Do", "labels": [], "priority": "Medium",
}
SAMPLE_PR = {"number": 7, "html_url": "https://github.com/owner/repo/pull/7"}
_SAMPLE_RUN = DevAgentRun(ticket_key="SCRUM-42", status="running", attempt_count=1)


def _run_with_state(state):
    return DevAgentRun(ticket_key="SCRUM-42", status="running", attempt_count=1, state=state)


@pytest.mark.anyio
async def test_process_ticket_success_advances_through_states_in_order():
    import dev_agent.orchestrator as orch

    state_calls: list = []

    async def _set_state(key, state, payload_merge=None):
        state_calls.append(state)

    # get_run reflects the state written so far, for _advance_state's validation read.
    async def _get_run(key):
        return _run_with_state(state_calls[-1] if state_calls else None)

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "set_state", side_effect=_set_state),
        patch.object(orch.db, "get_run", side_effect=_get_run),
        patch.object(orch.db, "finish_run", AsyncMock()),
        patch.object(orch.memgraph_client, "write_run_provenance", AsyncMock()),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
        patch.object(orch.jira_client, "transition_issue", AsyncMock(return_value=True)),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=True, returncode=0, result_text="done", num_turns=5)
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=SAMPLE_PR)),
        patch.object(orch.github_client, "get_pr_diff", AsyncMock(return_value="diff")),
        patch.object(orch.self_verify, "verify_pr", AsyncMock(return_value=orch.self_verify.VerifyVerdict(checked=True, addresses=True, confidence=0.9))),
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    assert state_calls == [lc.TRIAGED, lc.PLANNED, lc.IMPLEMENTING, lc.DEBUGGING, lc.REVIEWING, lc.SHIPPED]
    # Every consecutive pair must be a legal edge in the real state table.
    for a, b in zip(state_calls, state_calls[1:]):
        assert lc.can_transition(a, b), f"{a} -> {b} is not a legal transition"


@pytest.mark.anyio
async def test_process_ticket_no_pr_escalates_to_failed_from_debugging():
    import dev_agent.orchestrator as orch

    state_calls: list = []

    async def _set_state(key, state, payload_merge=None):
        state_calls.append(state)

    async def _get_run(key):
        return _run_with_state(state_calls[-1] if state_calls else None)

    with (
        patch.object(orch.db, "start_run", AsyncMock()),
        patch.object(orch.db, "set_state", side_effect=_set_state),
        patch.object(orch.db, "get_run", side_effect=_get_run),
        patch.object(orch.db, "finish_run", AsyncMock()),
        patch.object(orch.session_memory, "load_resume_context", AsyncMock(return_value=None)),
        patch.object(orch.session_memory, "record", AsyncMock()),
        patch.object(orch.memgraph_client, "merge_blocker", AsyncMock()),
        patch.object(orch.jira_client, "transition_issue", AsyncMock(return_value=True)),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch.jira_client, "add_comment", AsyncMock()),
        patch.object(orch.git_ops, "create_worktree", AsyncMock()),
        patch.object(orch.git_ops, "remove_worktree", AsyncMock()),
        patch.object(orch.claude_runner, "run_claude_code", AsyncMock(
            return_value=ClaudeRunResult(success=False, returncode=1, result_text="boom")
        )),
        patch.object(orch.github_client, "find_open_pr", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo", "JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.process_ticket(SAMPLE_TICKET)

    assert state_calls == [lc.TRIAGED, lc.PLANNED, lc.IMPLEMENTING, lc.DEBUGGING, lc.FAILED]
    assert lc.can_transition(lc.DEBUGGING, lc.FAILED)


@pytest.mark.anyio
async def test_advance_state_logs_but_does_not_raise_on_illegal_edge():
    """A logic bug in the sequencing must not crash ticket processing (loud, not fatal)."""
    import dev_agent.orchestrator as orch

    async def _get_run(key):
        return _run_with_state(lc.CLOSED)  # terminal — nothing should legally follow

    with (
        patch.object(orch.db, "get_run", side_effect=_get_run),
        patch.object(orch.db, "set_state", AsyncMock()) as mock_set,
    ):
        await orch._advance_state("SCRUM-1", lc.PLANNED)  # illegal from CLOSED

    mock_set.assert_awaited_once_with("SCRUM-1", lc.PLANNED)  # still persisted, just logged


# ---------------------------------------------------------------------------
# poll_and_process: resume a crashed (stuck non-terminal) run before new candidates
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_poll_and_process_resumes_active_run_before_new_candidates():
    import dev_agent.orchestrator as orch

    order: list = []
    stuck = _run_with_state(lc.IMPLEMENTING)

    async def _process_ticket(ticket):
        order.append(("process", ticket["key"]))

    with (
        patch.object(orch.backend, "preflight", AsyncMock(return_value="ok")),
        patch.object(orch.git_ops, "ensure_repo_cloned", AsyncMock()),
        patch.object(orch.db, "get_active_run", AsyncMock(return_value=stuck)),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(return_value=SAMPLE_TICKET)),
        patch.object(orch, "process_ticket", side_effect=_process_ticket),
        patch.object(orch.db, "should_attempt", AsyncMock(return_value=False)),  # no new candidates
        patch.object(orch.jira_client, "list_active_sprint_tickets", AsyncMock(return_value=[])),
        patch.object(orch.memgraph_client, "get_action_confidence", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.poll_and_process()

    assert order == [("process", "SCRUM-42")]  # the stuck run was resumed


@pytest.mark.anyio
async def test_poll_and_process_no_active_run_skips_resume():
    import dev_agent.orchestrator as orch

    with (
        patch.object(orch.backend, "preflight", AsyncMock(return_value="ok")),
        patch.object(orch.git_ops, "ensure_repo_cloned", AsyncMock()),
        patch.object(orch.db, "get_active_run", AsyncMock(return_value=None)),
        patch.object(orch, "process_ticket", AsyncMock()) as mock_process,
        patch.object(orch.jira_client, "list_active_sprint_tickets", AsyncMock(return_value=[])),
        patch.object(orch.memgraph_client, "get_action_confidence", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.poll_and_process()

    mock_process.assert_not_awaited()


@pytest.mark.anyio
async def test_poll_and_process_resume_failure_does_not_abort_new_candidates():
    """If resuming the crashed run itself blows up, the poll must still look for new work."""
    import dev_agent.orchestrator as orch

    stuck = _run_with_state(lc.IMPLEMENTING)

    with (
        patch.object(orch.backend, "preflight", AsyncMock(return_value="ok")),
        patch.object(orch.git_ops, "ensure_repo_cloned", AsyncMock()),
        patch.object(orch.db, "get_active_run", AsyncMock(return_value=stuck)),
        patch.object(orch.jira_client, "get_issue_detail", AsyncMock(side_effect=RuntimeError("jira down"))),
        patch.object(orch.jira_client, "list_active_sprint_tickets", AsyncMock(return_value=[])) as mock_list,
        patch.object(orch.memgraph_client, "get_action_confidence", AsyncMock(return_value=None)),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM"}),
    ):
        await orch.poll_and_process()  # must not raise

    mock_list.assert_awaited_once()  # still proceeded to look for new candidates
