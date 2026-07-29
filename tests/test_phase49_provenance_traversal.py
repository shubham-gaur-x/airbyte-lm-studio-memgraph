"""Phase 49 (B4): single-traversal provenance — meeting -> decision -> action item ->
ticket -> AgentRun -> PR -> files, and the reverse from a ticket key. One Cypher MATCH per
direction; row-grouping into a nested shape happens in pure Python (unit-testable without
a driver)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import memgraph_client as mc


def _row(**kw):
    base = dict(
        meeting_id=None, meeting_title=None, meeting_date=None, decisions=[],
        action_id=None, action_task=None, action_confidence=None, action_owner=None,
        ticket_key=None, ticket_summary=None,
        run_id=None, run_attempt=None, run_status=None, run_verified=None,
        pr_url=None, pr_number=None,
        commit_sha=None, commit_message=None, file_path=None, file_change_type=None,
    )
    base.update(kw)
    return base


# --- pure grouping: meeting -> ... -> files ---------------------------------
def test_group_meeting_provenance_no_action_items():
    rows = [_row(meeting_id="m1", meeting_title="Sync", decisions=[{"id": "d1", "text": "Ship it", "confidence": 0.9}])]
    out = mc._group_meeting_provenance(rows)
    assert out["meeting"] == {"id": "m1", "title": "Sync", "date": None}
    assert out["decisions"] == [{"id": "d1", "text": "Ship it", "confidence": 0.9}]
    assert out["action_items"] == []


def test_group_meeting_provenance_action_item_no_ticket_yet():
    rows = [_row(meeting_id="m1", meeting_title="Sync", action_id="a1", action_task="fix bug", action_confidence=0.9)]
    out = mc._group_meeting_provenance(rows)
    assert len(out["action_items"]) == 1
    ai = out["action_items"][0]
    assert ai["id"] == "a1" and ai["task"] == "fix bug"
    assert ai["ticket"] is None


def test_group_meeting_provenance_full_chain_one_run_one_pr_two_files():
    rows = [
        _row(meeting_id="m1", meeting_title="Sync", action_id="a1", action_task="fix bug",
             ticket_key="SCRUM-1", ticket_summary="Fix bug",
             run_id="r1", run_attempt=1, run_status="pr_opened", run_verified=True,
             pr_url="https://x/pull/1", pr_number=1,
             commit_sha="abc", commit_message="fix", file_path="a.py", file_change_type="modified"),
        _row(meeting_id="m1", meeting_title="Sync", action_id="a1", action_task="fix bug",
             ticket_key="SCRUM-1", ticket_summary="Fix bug",
             run_id="r1", run_attempt=1, run_status="pr_opened", run_verified=True,
             pr_url="https://x/pull/1", pr_number=1,
             commit_sha="abc", commit_message="fix", file_path="b.py", file_change_type="added"),
    ]
    out = mc._group_meeting_provenance(rows)
    assert len(out["action_items"]) == 1
    ai = out["action_items"][0]
    assert ai["ticket"] == {"key": "SCRUM-1", "summary": "Fix bug"}
    assert len(ai["agent_runs"]) == 1
    run = ai["agent_runs"][0]
    assert run["id"] == "r1" and run["verified"] is True
    assert run["pull_request"]["url"] == "https://x/pull/1" and run["pull_request"]["number"] == 1
    assert len(run["pull_request"]["commits"]) == 1
    commit = run["pull_request"]["commits"][0]
    assert commit["sha"] == "abc"
    assert {f["path"] for f in commit["files"]} == {"a.py", "b.py"}


def test_group_meeting_provenance_two_attempts_two_runs():
    rows = [
        _row(meeting_id="m1", action_id="a1", ticket_key="SCRUM-1",
             run_id="r1", run_attempt=1, run_status="failed", run_verified=None),
        _row(meeting_id="m1", action_id="a1", ticket_key="SCRUM-1",
             run_id="r2", run_attempt=2, run_status="pr_opened", run_verified=True,
             pr_url="https://x/pull/2", pr_number=2),
    ]
    out = mc._group_meeting_provenance(rows)
    runs = out["action_items"][0]["agent_runs"]
    assert {r["id"] for r in runs} == {"r1", "r2"}
    attempt2 = next(r for r in runs if r["id"] == "r2")
    assert attempt2["pull_request"]["number"] == 2


def test_group_meeting_provenance_empty_rows_returns_empty_shape():
    assert mc._group_meeting_provenance([]) == {"meeting": None, "decisions": [], "action_items": []}


# --- pure grouping: ticket -> ... (reverse direction) -----------------------
def test_group_ticket_provenance_shape():
    rows = [dict(
        ticket_key="SCRUM-1", ticket_summary="Fix bug",
        meeting_id="m1", meeting_title="Sync",
        action_id="a1", action_task="fix bug",
        run_id="r1", run_attempt=1, run_status="pr_opened", run_verified=True,
        pr_url="https://x/pull/1", pr_number=1,
        commit_sha="abc", file_path="a.py", file_change_type="modified",
    )]
    out = mc._group_ticket_provenance(rows)
    assert out["ticket"] == {"key": "SCRUM-1", "summary": "Fix bug"}
    assert out["meetings"] == [{"id": "m1", "title": "Sync"}]
    assert len(out["agent_runs"]) == 1
    assert out["agent_runs"][0]["pull_request"]["commits"][0]["files"][0]["path"] == "a.py"


def test_group_ticket_provenance_not_found():
    assert mc._group_ticket_provenance([]) is None


def test_group_ticket_provenance_carries_commit_message():
    """Regression (v5.1 Phase E live run): get_ticket_provenance's Cypher originally
    selected c.sha but not c.message, so the reverse traversal always showed
    commit.message=null even though get_meeting_provenance (forward direction) carried it
    correctly — an inconsistency between the two directions, not a fundamental gap."""
    rows = [dict(
        ticket_key="SCRUM-1", ticket_summary="Fix bug", meeting_id="m1", meeting_title="Sync",
        action_id="a1", action_task="fix bug",
        run_id="r1", run_attempt=1, run_status="pr_opened", run_verified=True,
        pr_url="https://x/pull/1", pr_number=1,
        commit_sha="abc", commit_message="fix the bug", file_path="a.py", file_change_type="modified",
    )]
    out = mc._group_ticket_provenance(rows)
    commit = out["agent_runs"][0]["pull_request"]["commits"][0]
    assert commit["message"] == "fix the bug"


# --- async readers issue the right Cypher ------------------------------------
def _driver_with_rows(rows):
    result = AsyncMock()
    result.__aiter__.return_value = iter(rows)
    session = AsyncMock()
    session.run = AsyncMock(return_value=result)
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver, session


@pytest.mark.anyio
async def test_get_meeting_provenance_issues_expected_traversal():
    driver, session = _driver_with_rows([_row(meeting_id="m1", meeting_title="Sync")])
    with patch.object(mc, "get_driver", return_value=driver):
        out = await mc.get_meeting_provenance("m1")
    cypher = session.run.call_args.args[0]
    for clause in ("PRODUCED", "FOLLOWS_UP", "TICKETED_AS", "IMPLEMENTS", "CONTAINS", "MODIFIES"):
        assert clause in cypher
    assert out["meeting"]["id"] == "m1"


@pytest.mark.anyio
async def test_get_ticket_provenance_issues_expected_traversal():
    driver, session = _driver_with_rows([dict(
        ticket_key="SCRUM-1", ticket_summary="Fix bug", meeting_id=None, meeting_title=None,
        action_id=None, action_task=None, run_id=None, run_attempt=None, run_status=None,
        run_verified=None, pr_url=None, pr_number=None, commit_sha=None, file_path=None,
        file_change_type=None,
    )])
    with patch.object(mc, "get_driver", return_value=driver):
        out = await mc.get_ticket_provenance("SCRUM-1")
    cypher = session.run.call_args.args[0]
    for clause in ("TICKETED_AS", "FOLLOWS_UP", "IMPLEMENTS", "CONTAINS", "MODIFIES"):
        assert clause in cypher
    assert "c.message" in cypher  # parity with get_meeting_provenance — see regression above
    assert out["ticket"]["key"] == "SCRUM-1"


@pytest.mark.anyio
async def test_get_ticket_provenance_not_found_returns_none():
    driver, _ = _driver_with_rows([])
    with patch.object(mc, "get_driver", return_value=driver):
        out = await mc.get_ticket_provenance("SCRUM-404")
    assert out is None


# --- endpoints ----------------------------------------------------------------
def test_provenance_endpoints_registered():
    from transform_service import main
    paths = {r.path for r in main.app.routes}
    assert "/graph/provenance/{meeting_id}" in paths
    assert "/graph/provenance/by-ticket/{ticket_key}" in paths


@pytest.mark.anyio
async def test_provenance_meeting_endpoint_shape():
    from transform_service import main
    with patch.object(main.memgraph_client, "get_meeting_provenance",
                      AsyncMock(return_value={"meeting": {"id": "m1"}, "decisions": [], "action_items": []})):
        out = await main.graph_provenance("m1")
    assert out["meeting"]["id"] == "m1"


@pytest.mark.anyio
async def test_provenance_by_ticket_endpoint_404_when_not_found():
    from fastapi import HTTPException
    from transform_service import main
    with patch.object(main.memgraph_client, "get_ticket_provenance", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await main.graph_provenance_by_ticket("SCRUM-404")
    assert exc_info.value.status_code == 404
