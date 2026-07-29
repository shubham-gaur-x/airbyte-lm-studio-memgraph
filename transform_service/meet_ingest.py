"""P1 live capture: Google Meet transcript fetch + Cloud Pub/Sub PULL consumer.

REQUIRES GCP creds (nothing else in the pipeline does). Flow:
  1. A Workspace Events subscription publishes ``meet.transcript.v2.fileGenerated`` to a topic.
  2. ``pull_and_stage`` pulls from a Pub/Sub PULL subscription over REST — no inbound tunnel,
     preserving the fully-local / no-tunnels principle.
  3. It fetches the text via the Meet REST API ``conferenceRecords.transcripts.entries``,
  4. stages a ``raw_meet_transcripts`` row (``db.insert_meet_transcript``) and runs ingestion.

Kept dependency-light (httpx only, no google-cloud libs). Auth is an OAuth access token
(``GOOGLE_ACCESS_TOKEN``) with Meet + Pub/Sub scopes; ``MEET_PUBSUB_SUBSCRIPTION`` is the
``projects/<p>/subscriptions/<s>`` to pull. Unset creds → disabled no-op (safe by default).
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
import structlog

from transform_service import db
from transform_service.utils import with_retry

log = structlog.get_logger()

MEET_API = "https://meet.googleapis.com/v2"
PUBSUB_API = "https://pubsub.googleapis.com/v1"


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def decode_event(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Decode a Pub/Sub message (base64 JSON) into {conference_record, transcript, title, start_time}.

    The fileGenerated event carries a resource name like
    ``conferenceRecords/{cr}/transcripts/{t}``.
    """
    try:
        raw = msg.get("message", {}).get("data", "")
        decoded = base64.b64decode(raw).decode("utf-8") if raw else "{}"
        ev = json.loads(decoded)
        name = ev.get("name") or ev.get("resourceName") or ""
        parts = name.split("/")
        cr = parts[1] if len(parts) > 1 else None
        tr = parts[3] if len(parts) > 3 else None
        if not cr or not tr:
            return None
        return {"conference_record": cr, "transcript": tr,
                "title": ev.get("title", ""), "start_time": ev.get("startTime")}
    except Exception as exc:
        log.warning("meet_ingest.decode_failed", error=str(exc))
        return None


@with_retry(max_attempts=3, base_delay=2.0)
async def fetch_transcript_entries(conference_record: str, transcript: str, token: str) -> str:
    """Concatenate all transcript entries into plain speaker-tagged text (paginated)."""
    url = f"{MEET_API}/conferenceRecords/{conference_record}/transcripts/{transcript}/entries"
    lines: List[str] = []
    page_token: Optional[str] = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: Dict[str, Any] = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(url, params=params, headers=_headers(token))
            resp.raise_for_status()
            data = resp.json()
            for e in data.get("transcriptEntries", []):
                who = (e.get("participant", "") or "").split("/")[-1] or "speaker"
                lines.append(f"{who}: {e.get('text', '')}")
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return "\n".join(lines)


async def pull_and_stage(process: Optional[Callable[[], Awaitable[None]]] = None) -> int:
    """Pull fileGenerated events, fetch transcripts, stage rows, then run ``process``.

    Returns the number of transcripts staged. Disabled no-op when creds are unset.
    """
    token = os.environ.get("GOOGLE_ACCESS_TOKEN", "").strip()
    sub = os.environ.get("MEET_PUBSUB_SUBSCRIPTION", "").strip()
    if not token or not sub:
        log.info("meet_ingest.disabled", reason="GOOGLE_ACCESS_TOKEN / MEET_PUBSUB_SUBSCRIPTION unset")
        return 0

    staged = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{PUBSUB_API}/{sub}:pull", headers=_headers(token),
                                 json={"maxMessages": 20})
        resp.raise_for_status()
        received = resp.json().get("receivedMessages", [])
        ack_ids: List[str] = []
        for msg in received:
            if msg.get("ackId"):
                ack_ids.append(msg["ackId"])
            payload = decode_event(msg)
            if not payload:
                continue
            text = await fetch_transcript_entries(
                payload["conference_record"], payload["transcript"], token,
            )
            await db.insert_meet_transcript(
                source_id=f"{payload['conference_record']}/{payload['transcript']}",
                title=payload.get("title", ""), transcript_text=text,
                conference_record=payload["conference_record"], start_time=payload.get("start_time"),
            )
            staged += 1
        if ack_ids:
            await client.post(f"{PUBSUB_API}/{sub}:acknowledge", headers=_headers(token),
                              json={"ackIds": ack_ids})

    log.info("meet_ingest.staged", count=staged)
    if staged and process is not None:
        await process()
    return staged
