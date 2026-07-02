"""Tests for Phase 22 — semantic_memory.py."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service.models import Attendee, ExtractedMeeting
from transform_service import semantic_memory


def _meeting(topics=None, attendees=None, follow_up=False) -> ExtractedMeeting:
    return ExtractedMeeting(
        title="Test meeting",
        kind="meeting",
        platform="Zoom",
        date=date(2026, 6, 30),
        summary="Alice and Bob discussed the roadmap and agreed on Q3 delivery.",
        topics=topics or ["roadmap", "Q3"],
        decisions=[],
        action_items=[],
        attendees=attendees or [
            Attendee(name="Alice", email="alice@example.com", role="host"),
            Attendee(name="Bob", email="bob@example.com", role="attendee"),
        ],
        follow_up_needed=follow_up,
    )


def _make_driver_session(run_return=None):
    result = AsyncMock()
    result.single = AsyncMock(return_value=run_return)
    session = AsyncMock()
    session.run.return_value = result
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver, session


def _mock_lm(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# extract_facts
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_extract_facts_valid_json_writes_facts():
    facts_json = json.dumps(["Alice leads backend", "API migration is Q3"])
    client = _mock_lm(facts_json)
    driver, session = _make_driver_session()

    with (
        patch("transform_service.semantic_memory._get_client", return_value=client),
        patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        count = await semantic_memory.extract_facts(_meeting(), "meeting-123")

    assert count == 2
    assert session.run.call_count == 2
    cypher = session.run.call_args_list[0].args[0]
    assert "MERGE (f:Fact" in cypher
    assert "HAS_FACT" in cypher


@pytest.mark.anyio
async def test_extract_facts_invalid_json_returns_zero_no_raise():
    client = _mock_lm("not valid json at all {[}")
    driver, session = _make_driver_session()

    with (
        patch("transform_service.semantic_memory._get_client", return_value=client),
        patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        count = await semantic_memory.extract_facts(_meeting(), "meeting-123")

    assert count == 0
    session.run.assert_not_called()


@pytest.mark.anyio
async def test_extract_facts_lm_error_returns_zero_no_raise():
    client = AsyncMock()
    client.chat.completions.create.side_effect = RuntimeError("LM Studio down")
    driver, session = _make_driver_session()

    with (
        patch("transform_service.semantic_memory._get_client", return_value=client),
        patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        count = await semantic_memory.extract_facts(_meeting(), "meeting-123")

    assert count == 0


# ---------------------------------------------------------------------------
# strengthen_relationships
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_strengthen_relationships_calls_knows_and_interested_in():
    driver, session = _make_driver_session()

    with patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver):
        await semantic_memory.strengthen_relationships(_meeting(), "meeting-123")

    # One KNOWS call + one INTERESTED_IN per topic (2 topics)
    assert session.run.call_count == 3
    cyphers = [c.args[0] for c in session.run.call_args_list]
    assert any("KNOWS" in c for c in cyphers)
    assert all("INTERESTED_IN" in c for c in cyphers[1:])


@pytest.mark.anyio
async def test_strengthen_relationships_no_emails_does_nothing():
    """If all attendees lack emails, no Cypher should run."""
    meeting = _meeting(attendees=[Attendee(name="No Email", role="attendee")])
    driver, session = _make_driver_session()

    with patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver):
        await semantic_memory.strengthen_relationships(meeting, "meeting-123")

    session.run.assert_not_called()


@pytest.mark.anyio
async def test_strengthen_relationships_unwind_pairs_cypher():
    """KNOWS cypher must use UNWIND and email < email2 ordering."""
    driver, session = _make_driver_session()

    with patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver):
        await semantic_memory.strengthen_relationships(_meeting(), "meeting-123")

    knows_call = session.run.call_args_list[0]
    cypher = knows_call.args[0]
    assert "UNWIND" in cypher
    assert "email1 < email2" in cypher


# ---------------------------------------------------------------------------
# consolidate_semantic
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_consolidate_semantic_returns_boosted_count():
    driver, session = _make_driver_session(run_return={"boosted": 7})

    with patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver):
        result = await semantic_memory.consolidate_semantic()

    assert result == {"facts_boosted": 7}
    cypher = session.run.call_args.args[0]
    assert "source_count % 3 = 0" in cypher
    assert "confidence" in cypher


@pytest.mark.anyio
async def test_consolidate_semantic_no_facts_returns_zero():
    driver, session = _make_driver_session(run_return=None)

    with patch("transform_service.semantic_memory.memgraph_client.get_driver", return_value=driver):
        result = await semantic_memory.consolidate_semantic()

    assert result == {"facts_boosted": 0}
