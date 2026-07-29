"""Phase 40 (P3b): person resolution wired into upsert_meeting_graph."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import memgraph_client
from transform_service.models import Attendee, ExtractedMeeting


def _meeting(attendees):
    return ExtractedMeeting(
        title="Sync", kind="meeting", platform="Zoom", date=date(2026, 7, 28),
        summary="s", attendees=attendees,
    )


def _tx_driver():
    tx = AsyncMock(); tx.run = AsyncMock(); tx.commit = AsyncMock()
    tx_cm = MagicMock(); tx_cm.__aenter__ = AsyncMock(return_value=tx); tx_cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock(); session.begin_transaction = AsyncMock(return_value=tx_cm)
    session_cm = MagicMock(); session_cm.__aenter__ = AsyncMock(return_value=session); session_cm.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock(); driver.session.return_value = session_cm
    return driver, tx


def _params(tx):
    merged = {}
    for c in tx.run.call_args_list:
        merged.setdefault("all", []).append(c.kwargs)
    return merged["all"]


@pytest.mark.anyio
async def test_email_variants_write_one_canonical_person():
    driver, tx = _tx_driver()
    meeting = _meeting([Attendee(name="Matteo", email="Matteo+standup@onixnet.com")])
    with (
        patch("transform_service.memgraph_client.get_driver", return_value=driver),
        patch.object(memgraph_client, "get_known_people", AsyncMock(return_value=[])),
        patch.object(memgraph_client.person_resolver, "load_roster", return_value=memgraph_client.person_resolver.Roster([])),
    ):
        await memgraph_client.upsert_meeting_graph(meeting, "src-1")
    # The Person MERGE must use the normalized canonical email, not the raw variant.
    person_calls = [kw for kw in _params(tx) if "email" in kw and kw.get("email", "").endswith("@onixnet.com")]
    assert person_calls and person_calls[0]["email"] == "matteo@onixnet.com"


@pytest.mark.anyio
async def test_no_email_attendee_is_held_for_review_not_dropped():
    driver, tx = _tx_driver()
    meeting = _meeting([Attendee(name="Ghost Attendee", email=None)])
    with (
        patch("transform_service.memgraph_client.get_driver", return_value=driver),
        patch.object(memgraph_client, "get_known_people", AsyncMock(return_value=[])),
        patch.object(memgraph_client.person_resolver, "load_roster", return_value=memgraph_client.person_resolver.Roster([])),
    ):
        await memgraph_client.upsert_meeting_graph(meeting, "src-2")
    cypher = "\n".join(c.args[0] for c in tx.run.call_args_list)
    assert "PersonReview" in cypher and "NEEDS_REVIEW" in cypher  # held, not dropped
    review = [kw for kw in _params(tx) if kw.get("name") == "Ghost Attendee" and "reason" in kw]
    assert review and review[0]["reason"] == "no-email-no-match"


@pytest.mark.anyio
async def test_roster_tracked_person_written_with_tracked_true():
    driver, tx = _tx_driver()
    roster = memgraph_client.person_resolver.Roster([
        memgraph_client.person_resolver.RosterEntry(name="Matteo Vaiente", email="matteo@onixnet.com", tracked=True),
    ])
    meeting = _meeting([Attendee(name="Matteo", email="matteo@onixnet.com")])
    with (
        patch("transform_service.memgraph_client.get_driver", return_value=driver),
        patch.object(memgraph_client, "get_known_people", AsyncMock(return_value=[])),
        patch.object(memgraph_client.person_resolver, "load_roster", return_value=roster),
    ):
        await memgraph_client.upsert_meeting_graph(meeting, "src-3")
    person = [kw for kw in _params(tx) if kw.get("email") == "matteo@onixnet.com"]
    assert person and person[0]["tracked"] is True
