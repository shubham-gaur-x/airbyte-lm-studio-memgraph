"""Phase 27: Tests for transform_service/action_agent.py.

Fixtures use real airbyte_agent_sdk Pydantic model instances (not dicts or
bare MagicMocks) for anything standing in for an SDK response record. A live
end-to-end run found that dict-shaped fixtures let `.get()`-based code pass
every test while failing for real, because both dicts and MagicMock silently
support `.get()` — real SDK models (Pydantic, extra="allow") do not.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from airbyte_agent_sdk.connectors.jira.models import (
    Issue,
    IssueComment,
    IssueCommentBody,
    IssueFields,
    IssueFieldsStatus,
    IssueTransition,
    IssueTransitionTo,
)

from transform_service import action_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_record(key: str, summary: str, description=None):
    fields = IssueFields(
        summary=summary,
        status=IssueFieldsStatus(name="To Do"),
    )
    # description/labels are dynamic (extra="allow"), same as the live API
    fields.description = description
    fields.labels = ["meeting-action-item"]
    return Issue(id=key, key=key, fields=fields)


def _comment(body_text: str):
    body = IssueCommentBody(
        type_="doc",
        version=1,
        content=[{"type": "paragraph", "content": [{"type": "text", "text": body_text}]}],
    )
    return IssueComment(id="1", body=body)


def _transition(tid: str, name: str, to_name: str):
    return IssueTransition(id=tid, name=name, to=IssueTransitionTo(name=to_name))


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
        _transition("21", "In Progress", "In Progress"),
        _transition("31", "In Review", "In Review"),
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
    jira = _mock_jira(comments=[_comment(action_agent.ACTION_AGENT_MARKER)])
    assert await action_agent.has_agent_draft(jira, "SCRUM-47") is True


@pytest.mark.anyio
async def test_has_agent_draft_false_when_no_marker():
    jira = _mock_jira(comments=[_comment("human comment")])
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
    assert "reschedule" in question


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
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 800


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


# ---------------------------------------------------------------------------
# post_draft / transition_to_review / process_action_items
# ---------------------------------------------------------------------------

def _enabled_env():
    return patch.dict(os.environ, {
        "ACTION_AGENT_ENABLED": "true",
        "AIRBYTE_AGENTS_CLIENT_ID": "cid",
        "AIRBYTE_AGENTS_CLIENT_SECRET": "cs",
        "AIRBYTE_AGENTS_CONNECTOR_ID": "conn",
        "JIRA_PROJECT_KEY": "SCRUM",
        "LM_STUDIO_MODEL": "test-model",
    })


def _patch_connector(jira):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=jira)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("transform_service.action_agent._make_connector", return_value=ctx)


@pytest.mark.anyio
async def test_post_draft_includes_marker_and_footer():
    jira = _mock_jira()
    await action_agent.post_draft(jira, "SCRUM-47", "The deliverable.", 4)
    kwargs = jira.issue_comments.create.call_args.kwargs
    assert kwargs["issue_id_or_key"] == "SCRUM-47"
    body_text = action_agent._adf_to_text(kwargs["body"])
    assert action_agent.ACTION_AGENT_MARKER in body_text
    assert "The deliverable." in body_text
    assert "4 nodes" in body_text


@pytest.mark.anyio
async def test_transition_to_review_picks_in_review_id():
    jira = _mock_jira()
    ok = await action_agent.transition_to_review(jira, "SCRUM-47")
    assert ok is True
    kwargs = jira.issue_transitions.create.call_args.kwargs
    assert kwargs["issue_id_or_key"] == "SCRUM-47"
    assert kwargs["transition"] == {"id": "31"}


@pytest.mark.anyio
async def test_transition_to_review_false_when_no_matching_transition():
    jira = _mock_jira()
    resp = MagicMock()
    resp.data = [_transition("41", "Done", "Done")]
    jira.issue_transitions.list = AsyncMock(return_value=resp)
    ok = await action_agent.transition_to_review(jira, "SCRUM-47")
    assert ok is False
    jira.issue_transitions.create.assert_not_called()


@pytest.mark.anyio
async def test_process_happy_path_drafts_comments_and_transitions():
    jira = _mock_jira(search_records=[_search_record("SCRUM-47", "Follow up", None)])
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.build_context", AsyncMock(return_value=("ctx", 2))),
        patch("transform_service.action_agent.draft_deliverable", AsyncMock(return_value="draft text")),
    ):
        result = await action_agent.process_action_items()

    assert result["drafted"] == 1
    assert result["failed"] == 0
    jira.issue_comments.create.assert_called_once()
    jira.issue_transitions.create.assert_called_once()


@pytest.mark.anyio
async def test_process_marker_present_repairs_transition_without_second_comment():
    jira = _mock_jira(
        search_records=[_search_record("SCRUM-47", "Follow up", None)],
        comments=[_comment(action_agent.ACTION_AGENT_MARKER)],
    )
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.draft_deliverable", AsyncMock()) as mock_draft,
    ):
        result = await action_agent.process_action_items()

    assert result["repaired"] == 1
    mock_draft.assert_not_called()
    jira.issue_comments.create.assert_not_called()
    jira.issue_transitions.create.assert_called_once()


@pytest.mark.anyio
async def test_process_llm_failure_writes_nothing():
    jira = _mock_jira(search_records=[_search_record("SCRUM-47", "Follow up", None)])
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.build_context", AsyncMock(return_value=("", 0))),
        patch("transform_service.action_agent.draft_deliverable", AsyncMock(return_value=None)),
    ):
        result = await action_agent.process_action_items()

    assert result["failed"] == 1
    assert result["drafted"] == 0
    jira.issue_comments.create.assert_not_called()
    jira.issue_transitions.create.assert_not_called()


@pytest.mark.anyio
async def test_process_one_bad_ticket_does_not_abort_batch():
    jira = _mock_jira(search_records=[
        _search_record("SCRUM-1", "bad", None),
        _search_record("SCRUM-2", "good", None),
    ])
    drafts = AsyncMock(side_effect=[RuntimeError("boom"), "fine"])
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.build_context", AsyncMock(return_value=("", 0))),
        patch("transform_service.action_agent.draft_deliverable", drafts),
    ):
        result = await action_agent.process_action_items()

    assert result["failed"] == 1
    assert result["drafted"] == 1


@pytest.mark.anyio
async def test_process_disabled_is_noop():
    with patch.dict(os.environ, {"ACTION_AGENT_ENABLED": "false"}):
        result = await action_agent.process_action_items()
    assert result == {"skipped": "disabled"}


@pytest.mark.anyio
async def test_process_missing_credentials_is_noop():
    env = {"ACTION_AGENT_ENABLED": "true", "AIRBYTE_AGENTS_CLIENT_ID": ""}
    with patch.dict(os.environ, env):
        result = await action_agent.process_action_items()
    assert result == {"skipped": "no_credentials"}
