"""P1 transcript capture — a clean seam so the transcript source is swappable.

Today's source is Google Meet via a Cloud Pub/Sub *pull* subscription (no inbound tunnel,
preserving the fully-local / no-tunnels principle): the Workspace Events subscription publishes
``google.workspace.meet.transcript.v2.fileGenerated``; a consumer pulls it, fetches the text
from the Meet REST API ``conferenceRecords.transcripts.entries`` (see ``meet_ingest``), and
writes a ``RawMeetTranscript`` row. ``graph_builder`` reads through this ``TranscriptSource``
seam, so a different producer (e.g. a notetaker bot) can be swapped in without touching any
downstream ingestion code.
"""
from __future__ import annotations

from typing import List, Protocol

from transform_service import db
from transform_service.models import RawMeetTranscript


class TranscriptSource(Protocol):
    async def fetch_pending(self, limit: int = 50) -> List[RawMeetTranscript]:
        ...


class DbTranscriptSource:
    """Default source: rows already staged in ``raw_meet_transcripts`` by the Pub/Sub-pull
    consumer. Swapping the upstream producer never touches ``graph_builder``."""

    async def fetch_pending(self, limit: int = 50) -> List[RawMeetTranscript]:
        return await db.get_unprocessed_transcripts(limit)


# graph_builder imports this; reassign to swap the source (e.g. a fixture in tests).
default_source: TranscriptSource = DbTranscriptSource()
