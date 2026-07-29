"""Phase 38 (P9): Blocker node + cleanup (jira_agent counters, extractor parse)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import extractor, jira_agent, memgraph_client
from transform_service.models import RawJiraIssue


# --- extractor._loads_lenient ---------------------------------------------
def test_loads_lenient_strict_json():
    assert extractor._loads_lenient('{"a": 1}') == {"a": 1}


def test_loads_lenient_prose_wrapped():
    assert extractor._loads_lenient('Sure! Here it is:\n{"a": 1}\nHope that helps') == {"a": 1}


def test_loads_lenient_rejects_non_dict_and_garbage():
    assert extractor._loads_lenient("[1, 2, 3]") is None
    assert extractor._loads_lenient("no json here") is None
    assert extractor._loads_lenient("") is None


# --- memgraph_client.update_action_jira_status returns match --------------
def _session_with_props_set(n):
    counters = MagicMock()
    counters.properties_set = n
    consumed = MagicMock()
    consumed.counters = counters
    result = AsyncMock()
    result.consume = AsyncMock(return_value=consumed)
    session = AsyncMock()
    session.run = AsyncMock(return_value=result)
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


@pytest.mark.anyio
async def test_update_action_jira_status_true_when_matched():
    with patch("transform_service.memgraph_client.get_driver", return_value=_session_with_props_set(3)):
        assert await memgraph_client.update_action_jira_status("SCRUM-1", "Done") is True


@pytest.mark.anyio
async def test_update_action_jira_status_false_when_no_match():
    with patch("transform_service.memgraph_client.get_driver", return_value=_session_with_props_set(0)):
        assert await memgraph_client.update_action_jira_status("SCRUM-404", "Done") is False


# --- jira_agent.sync_jira_issue propagates the real match -----------------
@pytest.mark.anyio
async def test_sync_jira_issue_returns_match_result():
    issue = RawJiraIssue(id="1", source_id="s", key="SCRUM-1", summary="x", status="Done")
    with (
        patch.object(jira_agent.memgraph_client, "update_action_jira_status", AsyncMock(return_value=False)),
        patch.object(jira_agent.db, "mark_processed", AsyncMock()),
    ):
        assert await jira_agent.sync_jira_issue(issue) is False
    with (
        patch.object(jira_agent.memgraph_client, "update_action_jira_status", AsyncMock(return_value=True)),
        patch.object(jira_agent.db, "mark_processed", AsyncMock()),
    ):
        assert await jira_agent.sync_jira_issue(issue) is True


# --- memgraph_client.merge_blocker ----------------------------------------
def _tx_driver():
    tx = AsyncMock()
    tx.run = AsyncMock()
    tx.commit = AsyncMock()
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=tx)
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.begin_transaction = AsyncMock(return_value=tx_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock()
    driver.session.return_value = session_cm
    return driver, tx


@pytest.mark.anyio
async def test_merge_blocker_merges_node_and_ticket_edge():
    driver, tx = _tx_driver()
    with patch("transform_service.memgraph_client.get_driver", return_value=driver):
        bid = await memgraph_client.merge_blocker("Workspace permission lockout", ticket_key="SCRUM-9")
    cypher = "\n".join(c.args[0] for c in tx.run.call_args_list)
    assert "MERGE (b:Blocker" in cypher
    assert "RAISES_BLOCKER" in cypher
    assert "CREATE (" not in cypher  # MERGE not CREATE
    tx.commit.assert_awaited_once()
    assert bid  # deterministic id returned


@pytest.mark.anyio
async def test_merge_blocker_empty_text_is_noop():
    driver, tx = _tx_driver()
    with patch("transform_service.memgraph_client.get_driver", return_value=driver):
        assert await memgraph_client.merge_blocker("   ") == ""
    tx.run.assert_not_awaited()
