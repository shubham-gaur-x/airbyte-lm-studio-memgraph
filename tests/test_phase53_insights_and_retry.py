"""Phase 53: fixes found during a live data-refresh run.

1. Insight queries (get_bridge_nodes/get_node_insights/get_influential_nodes/
   get_community_members) used COALESCE(n.name, n.email) for a display name — covering
   only Person. Every other node type (Meeting.title, ActionItem.task, Decision.text,
   Ticket.summary) came back name=null, making the "queryable insight" endpoints useless
   for anything but Person nodes.
2. upsert_meeting_graph had no retry, so a live run processing several meetings
   concurrently (asyncio.gather + a 3-way semaphore) hit real Memgraph
   "Cannot resolve conflicting transactions" errors whenever two meetings shared an
   attendee/org node written in overlapping transactions — very common in practice —
   and silently dropped that meeting's write instead of retrying.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import memgraph_client as mc
from transform_service.models import ExtractedMeeting


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
async def test_get_bridge_nodes_coalesce_covers_all_display_properties():
    driver, session = _driver_with_rows([])
    with patch.object(mc, "get_driver", return_value=driver):
        await mc.get_bridge_nodes(limit=5)
    cypher = session.run.call_args.args[0]
    for prop in ("n.name", "n.title", "n.task", "n.text", "n.summary", "n.email"):
        assert prop in cypher


@pytest.mark.anyio
async def test_get_node_insights_coalesce_covers_all_display_properties():
    driver, session = _driver_with_rows([])
    with patch.object(mc, "get_driver", return_value=driver):
        await mc.get_node_insights("some-id")
    cypher = session.run.call_args.args[0]
    for prop in ("n.name", "n.title", "n.task", "n.text", "n.summary", "n.email"):
        assert prop in cypher


def _meeting():
    return ExtractedMeeting(title="M", kind="meeting", platform="Zoom", date=date(2026, 7, 29), summary="s")


@pytest.mark.anyio
async def test_upsert_meeting_graph_retries_on_transient_conflict(monkeypatch):
    """Simulates Memgraph's real 'Cannot resolve conflicting transactions' error on
    attempt 1; must retry and succeed on attempt 2 rather than dropping the meeting."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())  # skip the real backoff delay

    call_count = {"n": 0}

    def _session_factory():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Cannot resolve conflicting transactions. Retry this transaction.")
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
        return session_cm

    driver = MagicMock()
    driver.session.side_effect = _session_factory

    with (
        patch.object(mc, "get_driver", return_value=driver),
        patch.object(mc, "get_known_people", AsyncMock(return_value=[])),
    ):
        node_id = await mc.upsert_meeting_graph(_meeting(), "src-conflict-1")

    assert node_id  # succeeded on the retry, not dropped
    assert call_count["n"] == 2  # exactly one retry


@pytest.mark.anyio
async def test_topic_merge_key_is_case_normalized():
    """Regression: the MERGE key used the raw-case topic name while topic_id already
    normalized to lowercase+strip, so two case variants of the same topic ('Talend to dbt'
    vs 'talend to dbt') created two Topic nodes sharing one uuid5 id -- confirmed live,
    fragmenting a real topic across nodes and understating it in bridge/community/pagerank
    insight queries, which key off these exact nodes."""
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

    meeting = ExtractedMeeting(
        title="M", kind="meeting", platform="Zoom", date=date(2026, 7, 29), summary="s",
        topics=["Talend to dbt Conversion Tool"],
    )
    with (
        patch.object(mc, "get_driver", return_value=driver),
        patch.object(mc, "get_known_people", AsyncMock(return_value=[])),
    ):
        await mc.upsert_meeting_graph(meeting, "src-topic-case-1")

    topic_call = next(c for c in tx.run.call_args_list if "MERGE (t:Topic" in c.args[0])
    assert topic_call.kwargs["name"] == "talend to dbt conversion tool"  # normalized, not raw
