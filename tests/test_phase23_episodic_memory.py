"""Tests for Phase 23 — episodic_memory.py."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service.models import Attendee, ExtractedMeeting
from transform_service import episodic_memory


def _meeting(follow_up: bool = False, summary: str = "General meeting summary.") -> ExtractedMeeting:
    return ExtractedMeeting(
        title="Test meeting",
        kind="meeting",
        platform="Zoom",
        date=date(2026, 6, 30),
        summary=summary,
        topics=["roadmap"],
        decisions=[],
        action_items=[],
        attendees=[
            Attendee(name="Alice", email="alice@example.com", role="host"),
        ],
        follow_up_needed=follow_up,
    )


def _make_driver(run_return=None, run_side_effect=None):
    result = AsyncMock()
    result.single = AsyncMock(return_value=run_return)
    if run_side_effect:
        result.single.side_effect = run_side_effect
    session = AsyncMock()
    session.run.return_value = result
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver, session


# ---------------------------------------------------------------------------
# link_temporal_chain
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_link_temporal_chain_with_prior_meeting_returns_true():
    prior_record = {"prior_id": "prior-meeting-abc"}
    driver, session = _make_driver(run_return=prior_record)

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        result = await episodic_memory.link_temporal_chain(
            "meeting-123", "2026-06-30", ["alice@example.com"]
        )

    assert result is True
    cypher = session.run.call_args.args[0]
    assert "PRECEDED_BY" in cypher
    assert "gap_days" in cypher


@pytest.mark.anyio
async def test_link_temporal_chain_no_prior_meeting_returns_false():
    driver, session = _make_driver(run_return=None)

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        result = await episodic_memory.link_temporal_chain(
            "meeting-123", "2026-06-30", ["alice@example.com"]
        )

    assert result is False


@pytest.mark.anyio
async def test_link_temporal_chain_no_emails_returns_false_no_db_call():
    driver, session = _make_driver()

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        result = await episodic_memory.link_temporal_chain("meeting-123", "2026-06-30", [])

    assert result is False
    session.run.assert_not_called()


# ---------------------------------------------------------------------------
# detect_causality
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_detect_causality_skips_when_follow_up_false():
    client = AsyncMock()
    driver, session = _make_driver()

    with (
        patch("transform_service.episodic_memory._get_client", return_value=client),
        patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        result = await episodic_memory.detect_causality(_meeting(follow_up=False), "meeting-123")

    assert result == 0
    client.chat.completions.create.assert_not_called()


@pytest.mark.anyio
async def test_detect_causality_lm_says_no_reference_returns_zero():
    resp_content = json.dumps({"references_prior": False, "reference_description": None})
    choice = MagicMock()
    choice.message.content = resp_content
    resp = MagicMock()
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    driver, session = _make_driver()
    # Mock the candidates query to return empty list
    async_iter = AsyncMock()
    async_iter.__aiter__ = MagicMock(return_value=iter([]))
    session.run.return_value = async_iter

    with (
        patch("transform_service.episodic_memory._get_client", return_value=client),
        patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        result = await episodic_memory.detect_causality(_meeting(follow_up=True), "meeting-123")

    assert result == 0


@pytest.mark.anyio
async def test_detect_causality_lm_error_returns_zero_no_raise():
    client = AsyncMock()
    client.chat.completions.create.side_effect = RuntimeError("LM Studio down")
    driver, session = _make_driver()

    with (
        patch("transform_service.episodic_memory._get_client", return_value=client),
        patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        result = await episodic_memory.detect_causality(_meeting(follow_up=True), "meeting-123")

    assert result == 0


# ---------------------------------------------------------------------------
# decay_relevance
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_decay_relevance_returns_updated_count():
    driver, session = _make_driver(run_return={"updated": 15})

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        result = await episodic_memory.decay_relevance()

    assert result == {"meetings_decayed": 15}
    cypher = session.run.call_args.args[0]
    assert "relevance_weight" in cypher
    assert "0.95" in cypher
    assert "0.1" in cypher


@pytest.mark.anyio
async def test_decay_relevance_no_meetings_returns_zero():
    driver, session = _make_driver(run_return=None)

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        result = await episodic_memory.decay_relevance()

    assert result == {"meetings_decayed": 0}


# ---------------------------------------------------------------------------
# log_memory_session
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_log_memory_session_creates_node_and_accessed_edges():
    driver, session = _make_driver()

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        session_id = await episodic_memory.log_memory_session(
            "What did Alice discuss?",
            "Alice discussed roadmap.",
            ["node-a", "node-b"],
        )

    assert isinstance(session_id, str) and len(session_id) > 0
    # One MERGE for the MemorySession + one ACCESSED per node
    assert session.run.call_count == 3
    cyphers = [c.args[0] for c in session.run.call_args_list]
    assert any("MemorySession" in c for c in cyphers)
    assert sum(1 for c in cyphers if "ACCESSED" in c) == 2


@pytest.mark.anyio
async def test_log_memory_session_empty_nodes_returns_id():
    driver, session = _make_driver()

    with patch("transform_service.episodic_memory.memgraph_client.get_driver", return_value=driver):
        session_id = await episodic_memory.log_memory_session("q", "a", [])

    assert session_id
    assert session.run.call_count == 1  # Only the MERGE for MemorySession
