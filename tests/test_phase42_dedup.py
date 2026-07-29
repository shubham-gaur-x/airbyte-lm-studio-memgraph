"""Phase 42 (P5): dedup of recurring action items."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from transform_service import dedup, jira_pusher
from transform_service.models import ActionItem, ExtractedMeeting
from datetime import date


# --- pure similarity -------------------------------------------------------
def test_cosine_identical_and_orthogonal():
    assert dedup.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert dedup.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert dedup.cosine([], [1.0]) == 0.0


def test_best_match_prefers_embedding_cosine():
    cands = [
        {"id": "a1", "task": "totally different words", "embedding": [1.0, 0.0], "jira_key": "SCRUM-1"},
        {"id": "a2", "task": "x", "embedding": [0.0, 1.0], "jira_key": "SCRUM-2"},
    ]
    m = dedup.best_match("new task", [0.99, 0.14], cands, threshold=0.9)
    assert m is not None and m["id"] == "a1" and m["score"] >= 0.9


def test_best_match_text_fallback_when_no_embeddings():
    cands = [{"id": "a1", "task": "draft the migration plan", "jira_key": "SCRUM-1"}]
    assert dedup.best_match("draft the migration plan", None, cands, threshold=0.9) is not None
    assert dedup.best_match("unrelated thing entirely", None, cands, threshold=0.9) is None


def test_best_match_none_below_threshold():
    cands = [{"id": "a1", "task": "abc", "embedding": [1.0, 0.0], "jira_key": "SCRUM-1"}]
    assert dedup.best_match("x", [0.0, 1.0], cands, threshold=0.9) is None


# --- jira_pusher dedup path -----------------------------------------------
def _meeting():
    return ExtractedMeeting(title="Weekly Sync", kind="meeting", platform="Zoom",
                            date=date(2026, 7, 28), summary="s")


@pytest.mark.anyio
async def test_duplicate_action_links_and_skips_ticket_creation():
    items = [ActionItem(owner="alice@x.com", task="draft the migration plan", confidence=0.9)]
    dup = {"id": "a-old", "jira_key": "SCRUM-1", "task": "draft the migration plan",
           "embedding": None, "meeting_title": "Last week"}
    with (
        patch.dict("os.environ", {"JIRA_ENABLED": "true", "JIRA_API_TOKEN": "x",
                                  "JIRA_PROJECT_KEY": "SCRUM", "JIRA_DEDUP_THRESHOLD": "0.9"}),
        patch.object(jira_pusher, "_get_active_sprint_id", AsyncMock(return_value=None)),
        patch.object(jira_pusher, "_create_jira_issue", AsyncMock(return_value="SCRUM-9")) as mock_create,
        patch.object(jira_pusher.memgraph_client, "get_open_actions_for_owner", AsyncMock(return_value=[dup])),
        patch.object(jira_pusher.memgraph_client, "link_action_mentioned_in", AsyncMock()) as mock_link,
        patch.object(jira_pusher.vector_memory, "embed_text", AsyncMock(return_value=None)),
        patch.object(jira_pusher, "_add_comment", AsyncMock()) as mock_comment,
    ):
        keys = await jira_pusher.push_action_items(items, _meeting(), "src-99")

    assert keys == []                      # no new ticket created
    mock_create.assert_not_awaited()
    mock_link.assert_awaited_once()        # MENTIONED_IN edge added
    mock_comment.assert_awaited_once()     # commented on the existing ticket


@pytest.mark.anyio
async def test_non_duplicate_action_still_creates_ticket():
    items = [ActionItem(owner="bob@x.com", task="set up brand new staging cluster", confidence=0.9)]
    with (
        patch.dict("os.environ", {"JIRA_ENABLED": "true", "JIRA_API_TOKEN": "x",
                                  "JIRA_PROJECT_KEY": "SCRUM", "JIRA_DEDUP_THRESHOLD": "0.9"}),
        patch.object(jira_pusher, "_get_active_sprint_id", AsyncMock(return_value=None)),
        patch.object(jira_pusher, "_create_jira_issue", AsyncMock(return_value="SCRUM-10")) as mock_create,
        patch.object(jira_pusher.memgraph_client, "get_open_actions_for_owner", AsyncMock(return_value=[])),
        patch.object(jira_pusher.memgraph_client, "update_action_jira_key", AsyncMock()),
        patch.object(jira_pusher.vector_memory, "embed_text", AsyncMock(return_value=None)),
    ):
        keys = await jira_pusher.push_action_items(items, _meeting(), "src-100")

    assert keys == ["SCRUM-10"]
    mock_create.assert_awaited_once()
