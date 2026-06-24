from __future__ import annotations

import asyncio
from typing import List

import structlog

from transform_service import db, memgraph_client
from transform_service.classifier import classify
from transform_service.extractor import extract_meeting
from transform_service.jira_pusher import push_action_items
from transform_service.models import RawCalendarEvent, RawEmail

log = structlog.get_logger()

_SCORE_THRESHOLD = 0.5


async def process_email(email: RawEmail) -> bool:
    bound = log.bind(source="email", source_id=email.source_id, step="classify")
    try:
        text = f"{email.subject}\n\n{email.body}"
        score = classify(text, {"from": email.from_email, "to": email.to_emails})

        if score < _SCORE_THRESHOLD:
            bound.info("graph_builder.skipped", score=round(score, 3))
            await db.mark_processed("raw_emails", email.id)
            return False

        bound = bound.bind(step="extract")
        meeting = await extract_meeting(text, "email")

        if not meeting:
            bound.warning("graph_builder.extract_failed")
            await db.mark_processed("raw_emails", email.id)
            return False

        bound = bound.bind(step="graph_write", meeting_title=meeting.title)
        node_id = await memgraph_client.upsert_meeting_graph(meeting, email.source_id)

        bound = bound.bind(step="jira_push")
        await push_action_items(meeting.action_items, meeting, node_id)

        await db.mark_processed("raw_emails", email.id)
        bound.info("graph_builder.email_processed", score=round(score, 3), node_id=node_id)
        return True

    except Exception as exc:
        log.error(
            "graph_builder.email_error",
            source_id=email.source_id,
            error=str(exc),
            exc_info=True,
        )
        return False


async def process_calendar_event(event: RawCalendarEvent) -> bool:
    bound = log.bind(source="calendar", source_id=event.source_id, step="classify")
    try:
        import json

        attendees_raw = event.attendees_json or "[]"
        try:
            attendees_data = json.loads(attendees_raw) if isinstance(attendees_raw, str) else attendees_raw
        except Exception:
            attendees_data = []

        attendees_count = len(attendees_data) if isinstance(attendees_data, list) else 0
        text = f"{event.title}\n\n{event.description or ''}"
        score = classify(
            text,
            {
                "start_time": event.start_time,
                "end_time": event.end_time,
                "attendees_count": attendees_count,
            },
        )

        if score < _SCORE_THRESHOLD:
            bound.info("graph_builder.skipped", score=round(score, 3))
            await db.mark_processed(event.source_table, event.id)
            return False

        bound = bound.bind(step="extract")
        # Pass the event date so extractor can fill it when LLM returns null
        event_date = event.start_time[:10] if event.start_time and len(event.start_time) >= 10 else None
        meeting = await extract_meeting(
            text, "calendar_event",
            context={"date": event_date, "platform": "google_calendar"},
        )

        if not meeting:
            bound.warning("graph_builder.extract_failed")
            await db.mark_processed(event.source_table, event.id)
            return False

        bound = bound.bind(step="graph_write", meeting_title=meeting.title)
        node_id = await memgraph_client.upsert_meeting_graph(meeting, event.source_id)

        bound = bound.bind(step="jira_push")
        await push_action_items(meeting.action_items, meeting, node_id)

        await db.mark_processed(event.source_table, event.id)
        bound.info("graph_builder.event_processed", score=round(score, 3), node_id=node_id)
        return True

    except Exception as exc:
        log.error(
            "graph_builder.event_error",
            source_id=event.source_id,
            error=str(exc),
            exc_info=True,
        )
        return False


async def process_new_emails() -> None:
    emails = await db.get_unprocessed_emails(limit=50)
    if not emails:
        return

    results = await asyncio.gather(*[process_email(e) for e in emails], return_exceptions=True)

    processed = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r is False)
    errors = sum(1 for r in results if isinstance(r, Exception))

    log.info(
        "graph_builder.batch_emails_done",
        total=len(emails),
        processed=processed,
        skipped=skipped,
        errors=errors,
    )


async def process_new_events() -> None:
    events = await db.get_unprocessed_events(limit=50)
    if not events:
        return

    results = await asyncio.gather(*[process_calendar_event(e) for e in events], return_exceptions=True)

    processed = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r is False)
    errors = sum(1 for r in results if isinstance(r, Exception))

    log.info(
        "graph_builder.batch_events_done",
        total=len(events),
        processed=processed,
        skipped=skipped,
        errors=errors,
    )
