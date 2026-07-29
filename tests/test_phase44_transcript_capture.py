"""Phase 44 (P1): Google Meet transcript capture ingestion path."""
from __future__ import annotations

import base64
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import graph_builder, meet_ingest, transcript_source
from transform_service.models import ExtractedMeeting, RawMeetTranscript


# --- model + source seam --------------------------------------------------
def test_raw_meet_transcript_defaults():
    t = RawMeetTranscript(id="1", source_id="cr/t")
    assert t.source_table == "raw_meet_transcripts" and t.processed is False


@pytest.mark.anyio
async def test_db_transcript_source_reads_db():
    with patch.object(transcript_source.db, "get_unprocessed_transcripts",
                      AsyncMock(return_value=[RawMeetTranscript(id="1", source_id="cr/t")])) as mock_get:
        out = await transcript_source.DbTranscriptSource().fetch_pending(limit=10)
    mock_get.assert_awaited_once_with(10)
    assert out[0].source_id == "cr/t"


# --- process_transcript: transcript is the PRIMARY extraction input --------
@pytest.mark.anyio
async def test_process_transcript_uses_transcript_text_as_primary():
    t = RawMeetTranscript(
        id="11111111-1111-1111-1111-111111111111", source_id="cr1/t1",
        title="Weekly Sync", transcript_text="Alice: we will ship Friday. Bob: I will test it.",
        calendar_description="calendar blurb (should be fallback only)",
    )
    meeting = ExtractedMeeting(title="Weekly Sync", kind="standup", platform="google_meet",
                               date=date(2026, 7, 28), summary="s")
    with (
        patch.object(graph_builder, "extract_meeting", AsyncMock(return_value=meeting)) as mock_extract,
        patch.object(graph_builder.memgraph_client, "upsert_meeting_graph", AsyncMock(return_value="node-1")),
        patch.object(graph_builder, "push_action_items", AsyncMock(return_value=[])),
        patch.object(graph_builder.db, "mark_processed", AsyncMock()) as mock_mark,
        patch.object(graph_builder.graph_algorithms, "run_fast_algorithms", AsyncMock(side_effect=RuntimeError("no db"))),
    ):
        ok = await graph_builder.process_transcript(t)

    assert ok is True
    mock_mark.assert_awaited_once()
    # The extraction text must contain the transcript, not just the calendar blurb.
    passed_text = mock_extract.await_args.args[0]
    assert "we will ship Friday" in passed_text
    assert mock_extract.await_args.args[1] == "meeting_transcript"


@pytest.mark.anyio
async def test_process_transcript_falls_back_to_calendar_when_no_transcript():
    t = RawMeetTranscript(
        id="22222222-2222-2222-2222-222222222222", source_id="cr2/t2",
        title="Planning workshop meeting",
        transcript_text="",
        calendar_description=(
            "Planning workshop meeting agenda: roadmap and KPI planning. "
            "Action item: draft the migration plan. Decision: budget approved."
        ),
    )
    meeting = ExtractedMeeting(title="Planning", kind="meeting", platform="google_meet",
                               date=date(2026, 7, 28), summary="s")
    with (
        patch.object(graph_builder, "extract_meeting", AsyncMock(return_value=meeting)) as mock_extract,
        patch.object(graph_builder.memgraph_client, "upsert_meeting_graph", AsyncMock(return_value="n")),
        patch.object(graph_builder, "push_action_items", AsyncMock(return_value=[])),
        patch.object(graph_builder.db, "mark_processed", AsyncMock()),
        patch.object(graph_builder.graph_algorithms, "run_fast_algorithms", AsyncMock(side_effect=RuntimeError("no db"))),
    ):
        ok = await graph_builder.process_transcript(t)
    assert ok is True
    assert "roadmap and KPI planning" in mock_extract.await_args.args[0]


# --- meet_ingest parsing (no live GCP) ------------------------------------
def test_decode_event_extracts_conference_and_transcript():
    data = base64.b64encode(json.dumps({
        "name": "conferenceRecords/abc123/transcripts/xyz789", "title": "Sync",
    }).encode()).decode()
    out = meet_ingest.decode_event({"message": {"data": data}, "ackId": "a1"})
    assert out == {"conference_record": "abc123", "transcript": "xyz789", "title": "Sync", "start_time": None}


def test_decode_event_rejects_malformed():
    assert meet_ingest.decode_event({"message": {"data": ""}}) is None


@pytest.mark.anyio
async def test_fetch_transcript_entries_concatenates_speaker_text():
    page = {"transcriptEntries": [
        {"participant": "participants/alice", "text": "we will ship Friday"},
        {"participant": "participants/bob", "text": "I will test it"},
    ]}
    resp = MagicMock(); resp.json.return_value = page; resp.raise_for_status = MagicMock()
    client = AsyncMock(); client.get = AsyncMock(return_value=resp)
    client_cm = MagicMock(); client_cm.__aenter__ = AsyncMock(return_value=client); client_cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(meet_ingest.httpx, "AsyncClient", return_value=client_cm):
        text = await meet_ingest.fetch_transcript_entries("cr", "t", "token")
    assert "alice: we will ship Friday" in text and "bob: I will test it" in text


@pytest.mark.anyio
async def test_pull_and_stage_disabled_without_creds():
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "", "MEET_PUBSUB_SUBSCRIPTION": ""}, clear=False):
        assert await meet_ingest.pull_and_stage() == 0
