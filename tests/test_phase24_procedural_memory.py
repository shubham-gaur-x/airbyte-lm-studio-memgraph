"""Tests for Phase 24 — procedural_memory.py."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service.models import Attendee, ExtractedMeeting
from transform_service.procedural_memory import (
    KNOWN_PROCEDURE_PATTERNS,
    _matches_pattern,
    match_to_procedure,
    discover_procedures,
)


def _meeting(
    kind: str = "meeting",
    topics: list[str] | None = None,
    attendees: list[Attendee] | None = None,
) -> ExtractedMeeting:
    return ExtractedMeeting(
        title="Test",
        kind=kind,  # type: ignore[arg-type]
        platform="Zoom",
        date=date(2026, 6, 30),
        summary="Summary.",
        topics=topics or [],
        decisions=[],
        action_items=[],
        attendees=attendees
        or [
            Attendee(name="Alice", email="alice@acme.com", role="host"),
            Attendee(name="Bob", email="bob@acme.com", role="attendee"),
        ],
    )


def _multi_org_meeting(topics: list[str]) -> ExtractedMeeting:
    return _meeting(
        topics=topics,
        attendees=[
            Attendee(name="Alice", email="alice@acme.com", role="host"),
            Attendee(name="Client", email="client@client.com", role="attendee"),
        ],
    )


# ---------------------------------------------------------------------------
# _matches_pattern — pure Python, no async, no mocks
# ---------------------------------------------------------------------------

class TestMatchesPattern:
    def test_sprint_planning_matches(self):
        m = _meeting(
            kind="meeting",
            topics=["sprint planning", "backlog"],
            attendees=[
                Attendee(name="A", email="a@x.com", role="host"),
                Attendee(name="B", email="b@x.com", role="attendee"),
                Attendee(name="C", email="c@x.com", role="attendee"),
            ],
        )
        assert _matches_pattern(m, KNOWN_PROCEDURE_PATTERNS["sprint_planning"])

    def test_sprint_planning_too_few_attendees_no_match(self):
        m = _meeting(kind="meeting", topics=["sprint backlog"])
        assert not _matches_pattern(m, KNOWN_PROCEDURE_PATTERNS["sprint_planning"])

    def test_one_on_one_exactly_two_matches(self):
        assert _matches_pattern(_meeting(), KNOWN_PROCEDURE_PATTERNS["one_on_one"])

    def test_one_on_one_three_attendees_no_match(self):
        m = _meeting(
            attendees=[
                Attendee(name="A", email="a@x.com", role="host"),
                Attendee(name="B", email="b@x.com", role="attendee"),
                Attendee(name="C", email="c@x.com", role="attendee"),
            ]
        )
        assert not _matches_pattern(m, KNOWN_PROCEDURE_PATTERNS["one_on_one"])

    def test_incident_response_matches_keyword(self):
        m = _meeting(topics=["production outage", "hotfix deployment"])
        assert _matches_pattern(m, KNOWN_PROCEDURE_PATTERNS["incident_response"])

    def test_client_review_requires_multi_org(self):
        # Same org — should not match
        same_org = _meeting(topics=["client demo feedback"])
        assert not _matches_pattern(same_org, KNOWN_PROCEDURE_PATTERNS["client_review"])

        # Multi org — should match
        multi = _multi_org_meeting(["client demo feedback"])
        assert _matches_pattern(multi, KNOWN_PROCEDURE_PATTERNS["client_review"])

    def test_retrospective_matches(self):
        m = _meeting(topics=["retrospective", "what went well"])
        assert _matches_pattern(m, KNOWN_PROCEDURE_PATTERNS["retrospective"])

    def test_no_topic_match_returns_false(self):
        m = _meeting(topics=["quarterly planning", "okrs"])
        for proc_name in ["sprint_planning", "incident_response", "retrospective"]:
            assert not _matches_pattern(m, KNOWN_PROCEDURE_PATTERNS[proc_name])


# ---------------------------------------------------------------------------
# match_to_procedure — mock driver
# ---------------------------------------------------------------------------

def _make_driver():
    session = AsyncMock()
    session.run.return_value = AsyncMock()
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver, session


@pytest.mark.anyio
async def test_match_to_procedure_sprint_planning_writes_cypher():
    m = _meeting(
        kind="meeting",
        topics=["sprint", "backlog"],
        attendees=[
            Attendee(name="A", email="a@x.com", role="host"),
            Attendee(name="B", email="b@x.com", role="attendee"),
            Attendee(name="C", email="c@x.com", role="attendee"),
        ],
    )
    driver, session = _make_driver()

    with patch("transform_service.procedural_memory.memgraph_client.get_driver", return_value=driver):
        matched = await match_to_procedure(m, "meeting-abc", ["a@x.com", "b@x.com", "c@x.com"])

    assert "sprint_planning" in matched
    cyphers = [c.args[0] for c in session.run.call_args_list]
    assert any("FOLLOWS_PROCEDURE" in c for c in cyphers)
    assert any("occurrence_count" in c for c in cyphers)


@pytest.mark.anyio
async def test_match_to_procedure_no_match_returns_empty():
    # 4 attendees: too many for one_on_one, wrong topics for everything else
    m = _meeting(
        topics=["quarterly review", "okrs"],
        attendees=[
            Attendee(name="A", email="a@x.com", role="host"),
            Attendee(name="B", email="b@x.com", role="attendee"),
            Attendee(name="C", email="c@x.com", role="attendee"),
            Attendee(name="D", email="d@x.com", role="attendee"),
        ],
    )
    driver, session = _make_driver()

    with patch("transform_service.procedural_memory.memgraph_client.get_driver", return_value=driver):
        matched = await match_to_procedure(m, "meeting-abc", [])

    assert matched == []
    session.run.assert_not_called()


@pytest.mark.anyio
async def test_match_to_procedure_multiple_patterns_can_match():
    """A retrospective that also feels like sprint_planning can match both."""
    m = _meeting(
        kind="meeting",
        topics=["sprint retrospective", "backlog", "what went well"],
        attendees=[
            Attendee(name="A", email="a@x.com", role="host"),
            Attendee(name="B", email="b@x.com", role="attendee"),
            Attendee(name="C", email="c@x.com", role="attendee"),
        ],
    )
    driver, session = _make_driver()

    with patch("transform_service.procedural_memory.memgraph_client.get_driver", return_value=driver):
        matched = await match_to_procedure(m, "meeting-abc", [])

    assert "sprint_planning" in matched
    assert "retrospective" in matched


# ---------------------------------------------------------------------------
# discover_procedures — mock driver with controlled data
# ---------------------------------------------------------------------------

class _AsyncIter:
    """Proper async iterator for mocking neo4j async result."""
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


def _make_discover_driver(rows: list[dict]):
    """Driver whose session.run() returns an async-iterable result."""
    call_count = 0

    async def run_side_effect(cypher, **kwargs):
        nonlocal call_count
        call_count += 1
        # Only the first query (MATCH m:Meeting) returns rows; subsequent writes just succeed
        if "MATCH (m:Meeting)" in cypher and "FOLLOWS_PROCEDURE" in cypher:
            return _AsyncIter(rows)
        # For write Cypher (MERGE etc.) return a simple AsyncMock
        m = AsyncMock()
        m.__aiter__ = MagicMock(return_value=iter([]))
        return m

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


@pytest.mark.anyio
async def test_discover_procedures_creates_procedure_when_cluster_size_met():
    rows = [
        {"meeting_id": f"m{i}", "community_ids": [42], "topic_names": ["planning", "roadmap"]}
        for i in range(5)
    ]
    driver = _make_discover_driver(rows)

    with patch("transform_service.procedural_memory.memgraph_client.get_driver", return_value=driver):
        result = await discover_procedures()

    assert result["clusters_found"] >= 1
    assert result["procedures_created"] >= 1


@pytest.mark.anyio
async def test_discover_procedures_does_not_create_when_cluster_too_small():
    rows = [
        {"meeting_id": f"m{i}", "community_ids": [99], "topic_names": ["planning"]}
        for i in range(3)
    ]
    driver = _make_discover_driver(rows)

    with patch("transform_service.procedural_memory.memgraph_client.get_driver", return_value=driver):
        result = await discover_procedures()

    assert result["procedures_created"] == 0
