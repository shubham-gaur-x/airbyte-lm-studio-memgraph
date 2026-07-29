"""Phase 41 (P4): per-item confidence gating for Jira creation + dev-agent pickup."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import jira_pusher, memgraph_client
from transform_service.models import ActionItem, ExtractedMeeting


def _meeting():
    return ExtractedMeeting(title="M", kind="meeting", platform="Zoom", date=date(2026, 7, 28), summary="s")


# --- ActionItem.confidence default ----------------------------------------
def test_action_item_confidence_defaults_to_one():
    assert ActionItem(owner="a", task="t").confidence == 1.0


# --- jira_pusher gates low confidence -------------------------------------
@pytest.mark.anyio
async def test_low_confidence_action_is_held_for_review_not_ticketed():
    items = [
        ActionItem(owner="a", task="solid task", confidence=0.9),
        ActionItem(owner="b", task="shaky task", confidence=0.3),
    ]
    with (
        patch.dict("os.environ", {"JIRA_ENABLED": "true", "JIRA_API_TOKEN": "x",
                                  "JIRA_PROJECT_KEY": "SCRUM", "JIRA_CONFIDENCE_THRESHOLD": "0.6"}),
        patch.object(jira_pusher, "_get_active_sprint_id", AsyncMock(return_value=None)),
        patch.object(jira_pusher, "_create_jira_issue", AsyncMock(return_value="SCRUM-1")) as mock_create,
        patch.object(jira_pusher.memgraph_client, "update_action_jira_key", AsyncMock()),
        patch.object(jira_pusher.memgraph_client, "mark_action_needs_review", AsyncMock()) as mock_review,
    ):
        keys = await jira_pusher.push_action_items(items, _meeting(), "src-1")

    assert keys == ["SCRUM-1"]           # only the confident item became a ticket
    assert mock_create.await_count == 1
    mock_review.assert_awaited_once()    # the shaky one was held for review
    assert round(mock_review.await_args.args[1], 2) == 0.3


# --- get_action_confidence ------------------------------------------------
def _single_driver(value):
    rec = {"c": value} if value is not None else None
    result = AsyncMock()
    result.single = AsyncMock(return_value=rec)
    session = AsyncMock()
    session.run = AsyncMock(return_value=result)
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


@pytest.mark.anyio
async def test_get_action_confidence_value_and_none():
    with patch("transform_service.memgraph_client.get_driver", return_value=_single_driver(0.42)):
        assert await memgraph_client.get_action_confidence("SCRUM-1") == 0.42
    with patch("transform_service.memgraph_client.get_driver", return_value=_single_driver(None)):
        assert await memgraph_client.get_action_confidence("SCRUM-404") is None


# --- dev-agent independent pre-pickup gate --------------------------------
@pytest.mark.anyio
async def test_find_sprint_candidates_drops_low_confidence_ticket():
    import dev_agent.orchestrator as orch

    async def _conf(key):
        return {"SCRUM-1": 0.3, "SCRUM-2": 0.9}.get(key)

    with (
        patch.object(orch.jira_client, "list_active_sprint_tickets", AsyncMock(return_value=[
            {"key": "SCRUM-1"}, {"key": "SCRUM-2"},
        ])),
        patch.object(orch.memgraph_client, "get_action_confidence", side_effect=_conf),
        patch.dict("os.environ", {"JIRA_PROJECT_KEY": "SCRUM", "DEV_AGENT_CONFIDENCE_THRESHOLD": "0.6"}),
    ):
        eligible = await orch.find_sprint_candidates()

    assert [t["key"] for t in eligible] == ["SCRUM-2"]  # low-confidence SCRUM-1 held back
