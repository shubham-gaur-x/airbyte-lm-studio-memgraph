"""Phase 52: db.py Airbyte-preferred-with-fallback logic.

Regression for a live bug found during a real data refresh: once Airbyte tables
(messages_details / raw_gcal_events) exist, get_unprocessed_emails/events returned
early on table *existence* alone, even with zero unprocessed rows — so manual/
seeded rows in raw_emails/raw_calendar_events sat unprocessed forever once Airbyte
was wired up, contradicting the functions' own "preferred with fallback" docstrings.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import db as db_mod


def _mock_pool(fetchval_return, fetch_side_effect):
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn_cm)
    return pool, conn


_RAW_EMAIL_ROW = {
    "id": "u1", "source_id": "raw-1", "subject": "Hi", "from_email": "a@x.com",
    "to_emails": ["b@x.com"], "body": "body text", "received_at": "2026-07-29 00:00:00",
    "processed": False,
}
_AIRBYTE_EMAIL_ROW = {
    "source_id": "gmail-1", "subject": "Hi", "from_email": "a@x.com",
    "to_email": "b@x.com", "snippet": "snip", "received_at": None,
}
_RAW_EVENT_ROW = {
    "id": "u1", "source_id": "raw-ev-1", "title": "Sync", "description": None,
    "start_time": "2026-07-29 00:00:00", "end_time": "2026-07-29 00:30:00",
    "attendees_json": None, "processed": False,
}
_AIRBYTE_EVENT_ROW = {
    "id": "arb-1", "source_id": "gcal-1", "title": "Sync", "description": None,
    "start_time": "2026-07-29T00:00:00", "end_time": "2026-07-29T00:30:00",
    "attendees_json": None, "processed": False,
}


@pytest.mark.anyio
async def test_get_unprocessed_emails_uses_airbyte_when_rows_present():
    pool, conn = _mock_pool(True, [[_AIRBYTE_EMAIL_ROW]])
    with patch.object(db_mod, "get_pool", AsyncMock(return_value=pool)):
        out = await db_mod.get_unprocessed_emails(limit=10)
    assert len(out) == 1 and out[0].source_id == "gmail-1"
    conn.fetch.assert_awaited_once()  # never touched raw_emails


@pytest.mark.anyio
async def test_get_unprocessed_emails_falls_back_when_airbyte_table_empty():
    pool, conn = _mock_pool(True, [[], [_RAW_EMAIL_ROW]])
    with patch.object(db_mod, "get_pool", AsyncMock(return_value=pool)):
        out = await db_mod.get_unprocessed_emails(limit=10)
    assert len(out) == 1 and out[0].source_id == "raw-1"
    assert conn.fetch.await_count == 2  # tried Airbyte, then fell through


@pytest.mark.anyio
async def test_get_unprocessed_emails_falls_back_when_airbyte_table_absent():
    pool, conn = _mock_pool(False, [[_RAW_EMAIL_ROW]])
    with patch.object(db_mod, "get_pool", AsyncMock(return_value=pool)):
        out = await db_mod.get_unprocessed_emails(limit=10)
    assert len(out) == 1 and out[0].source_id == "raw-1"
    conn.fetch.assert_awaited_once()  # only the raw_emails query


@pytest.mark.anyio
async def test_get_unprocessed_events_uses_airbyte_when_rows_present():
    pool, conn = _mock_pool(True, [[_AIRBYTE_EVENT_ROW]])
    with patch.object(db_mod, "get_pool", AsyncMock(return_value=pool)):
        out = await db_mod.get_unprocessed_events(limit=10)
    assert len(out) == 1 and out[0].source_id == "gcal-1"


@pytest.mark.anyio
async def test_get_unprocessed_events_falls_back_when_airbyte_table_empty():
    pool, conn = _mock_pool(True, [[], [_RAW_EVENT_ROW]])
    with patch.object(db_mod, "get_pool", AsyncMock(return_value=pool)):
        out = await db_mod.get_unprocessed_events(limit=10)
    assert len(out) == 1 and out[0].source_id == "raw-ev-1"
    assert conn.fetch.await_count == 2
