"""Phase 27: Tests for transform_service/action_agent.py."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import action_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_record(key: str, summary: str, description=None):
    return {"key": key, "fields": {"summary": summary, "description": description}}


def _mock_jira(search_records=None, comments=None):
    jira = MagicMock()
    search_resp = MagicMock()
    search_resp.data = search_records or []
    jira.issues.api_search = AsyncMock(return_value=search_resp)
    comments_resp = MagicMock()
    comments_resp.data = comments or []
    jira.issue_comments.list = AsyncMock(return_value=comments_resp)
    jira.issue_comments.create = AsyncMock()
    transitions_resp = MagicMock()
    transitions_resp.data = [
        {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
        {"id": "31", "name": "In Review", "to": {"name": "In Review"}},
    ]
    jira.issue_transitions.list = AsyncMock(return_value=transitions_resp)
    jira.issue_transitions.create = AsyncMock()
    return jira


# ---------------------------------------------------------------------------
# _adf_to_text / _records
# ---------------------------------------------------------------------------

def test_adf_to_text_flattens_nested_document():
    adf = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "world"}]},
        ],
    }
    assert action_agent._adf_to_text(adf) == "Hello world"


def test_adf_to_text_passes_plain_string_through():
    assert action_agent._adf_to_text("already plain") == "already plain"


def test_adf_to_text_handles_none():
    assert action_agent._adf_to_text(None) == ""


def test_records_prefers_data_attribute():
    resp = MagicMock()
    resp.data = [1, 2]
    assert action_agent._records(resp) == [1, 2]


def test_records_handles_plain_list():
    assert action_agent._records([{"key": "A-1"}]) == [{"key": "A-1"}]


# ---------------------------------------------------------------------------
# find_eligible_tickets
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_find_eligible_tickets_builds_bounded_jql_and_maps_fields():
    jira = _mock_jira(search_records=[
        _search_record("SCRUM-47", "Action requested", {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "details"}]}],
        }),
    ])
    with patch.dict(os.environ, {"JIRA_PROJECT_KEY": "SCRUM", "ACTION_AGENT_BATCH_SIZE": "5"}):
        tickets = await action_agent.find_eligible_tickets(jira)

    assert tickets == [{"key": "SCRUM-47", "summary": "Action requested", "description": "details"}]
    jql = jira.issues.api_search.call_args.kwargs["jql"]
    assert 'project = "SCRUM"' in jql
    assert 'status = "To Do"' in jql
    assert 'labels = "meeting-action-item"' in jql


@pytest.mark.anyio
async def test_find_eligible_tickets_respects_batch_size():
    records = [_search_record(f"SCRUM-{i}", f"t{i}") for i in range(10)]
    jira = _mock_jira(search_records=records)
    with patch.dict(os.environ, {"ACTION_AGENT_BATCH_SIZE": "3"}):
        tickets = await action_agent.find_eligible_tickets(jira)
    assert len(tickets) == 3


# ---------------------------------------------------------------------------
# has_agent_draft
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_has_agent_draft_true_when_marker_comment_exists():
    jira = _mock_jira(comments=[
        {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": action_agent.ACTION_AGENT_MARKER}]},
        ]}},
    ])
    assert await action_agent.has_agent_draft(jira, "SCRUM-47") is True


@pytest.mark.anyio
async def test_has_agent_draft_false_when_no_marker():
    jira = _mock_jira(comments=[
        {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "human comment"}]},
        ]}},
    ])
    assert await action_agent.has_agent_draft(jira, "SCRUM-47") is False


# ---------------------------------------------------------------------------
# build_context / draft_deliverable
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_build_context_uses_full_memory_query():
    fake = AsyncMock(return_value={
        "answer": "Matteo hosts the migration meetings.",
        "session_id": "s1",
        "nodes_used": [{"id": "a"}, {"id": "b"}],
        "context_summary": {"people_found": 1, "topics_found": 0},
    })
    with patch("transform_service.action_agent.memory_retrieval.full_memory_query", fake):
        text, count = await action_agent.build_context("Follow up with Matteo", "reschedule")

    assert "Matteo hosts" in text
    assert count == 2
    question = fake.call_args.args[0]
    assert "Follow up with Matteo" in question


@pytest.mark.anyio
async def test_build_context_survives_memory_failure():
    fake = AsyncMock(side_effect=RuntimeError("graph down"))
    with patch("transform_service.action_agent.memory_retrieval.full_memory_query", fake):
        text, count = await action_agent.build_context("t", "d")
    assert text == ""
    assert count == 0


@pytest.mark.anyio
async def test_draft_deliverable_returns_text():
    msg = MagicMock()
    msg.message.content = "Hi Matteo, could we move our sync to Thursday?"
    resp = MagicMock()
    resp.choices = [msg]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    with (
        patch("transform_service.action_agent._get_client", return_value=client),
        patch.dict(os.environ, {"LM_STUDIO_MODEL": "test-model"}),
    ):
        draft = await action_agent.draft_deliverable("Follow up", "reschedule", "context")
    assert "Thursday" in draft


@pytest.mark.anyio
async def test_draft_deliverable_returns_none_on_llm_failure():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("LM down"))
    with (
        patch("transform_service.action_agent._get_client", return_value=client),
        patch.dict(os.environ, {"LM_STUDIO_MODEL": "test-model"}),
    ):
        draft = await action_agent.draft_deliverable("t", "d", "c")
    assert draft is None


@pytest.mark.anyio
async def test_draft_deliverable_returns_none_on_empty_answer():
    msg = MagicMock()
    msg.message.content = "   "
    resp = MagicMock()
    resp.choices = [msg]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    with (
        patch("transform_service.action_agent._get_client", return_value=client),
        patch.dict(os.environ, {"LM_STUDIO_MODEL": "test-model"}),
    ):
        draft = await action_agent.draft_deliverable("t", "d", "c")
    assert draft is None
