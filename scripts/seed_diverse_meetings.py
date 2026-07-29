"""Data refresh: seed several diverse, realistic meetings through the real local pipeline.

Not a fixture — inserts real rows into Postgres staging tables and runs the actual
graph_builder path (classify -> route by type -> extract via LM Studio -> Memgraph write
-> jira_pusher with confidence gating + dedup). Exercises P1 (transcript), P3 (an
attendee with no email), P5 (a recurring task mentioned twice), and P6 (standup vs
planning vs review vs 1:1 routing) with one run.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()


STANDUP_SUBJECT = "Daily Standup - QA AI Adoption Pilot"
STANDUP_BODY = """\
Standup notes, 10 min, async in Slack thread synced here.

Attendees: Femi Oduwole (femi.oduwole@onixnet.com), Jacob Barka (jacob.barka@onixnet.com).

Status:
- Femi: finished the KPI dashboard filters yesterday, today picking up the export bug.
- Jacob: blocked on the staging credentials from IT, will follow up directly.

Action items:
- Femi to fix the CSV export bug in the KPI dashboard by tomorrow.
"""

PLANNING_TITLE = "QA AI Pilot - Sprint Planning"
PLANNING_DESC = """\
Sprint planning for the QA AI adoption pilot, 45 minutes.

Attendees: Matteo Vaiente (matteo@onixnet.com), Femi Oduwole (femi.oduwole@onixnet.com),
Priya Nair (priya.nair@onixnet.com).

Decisions:
- We decided to prioritize the export bug fix over the new onboarding flow this sprint.
- We agreed to move the KPI dashboard to a weekly cadence instead of daily.

Action items:
- Priya to draft the migration plan for moving the KPI dashboard to the new data warehouse
  by end of next week. This requires writing new ETL code.
- Matteo to review the pilot's budget with finance.
"""

REVIEW_TITLE = "CBS - Demo & Review"
REVIEW_DESC = """\
Demo of the KPI dashboard export feature to the CBS stakeholders, followed by feedback.

Attendees: Matteo Vaiente (matteo@onixnet.com), Mark Johnston (mark.johnston@onixnet.com).

Feedback:
- The export button placement was confusing; stakeholders want it moved to the toolbar.
- Overall positive reception of the filter redesign.

Action items:
- Femi to move the export button to the toolbar based on demo feedback.
"""

# Recurring meeting mentioning the SAME task as PLANNING above (P5 dedup exercise).
FOLLOWUP_SUBJECT = "QA AI Pilot : Weekly touchpoint"
FOLLOWUP_BODY = """\
Weekly touchpoint, 20 minutes.

Attendees: Priya Nair (priya.nair@onixnet.com), Matteo Vaiente (matteo@onixnet.com).

Status: migration plan still in progress, on track.

Action items:
- Priya to draft the migration plan for moving the KPI dashboard to the new data warehouse.
"""

# A 1:1, and an attendee with NO email (P3 review-queue exercise).
ONE_ON_ONE_TITLE = "Matteo / Femi 1:1"
ONE_ON_ONE_DESC = """\
Regular 1:1 catch-up, 30 minutes.

Attendees: Matteo Vaiente (matteo@onixnet.com), Femi (no email on file for this call).

Discussion: career growth, workload balance on the QA AI pilot.

Action items:
- Femi to send Matteo a list of stretch projects he is interested in by Friday.
"""

# A Meet transcript (P1) — the primary extraction input, not a calendar description.
TRANSCRIPT_TITLE = "K8s Benchmarking Sync"
TRANSCRIPT_TEXT = """\
jacob: quick sync on the k8s benchmarking numbers.
jacob: we saw a 15 percent latency improvement on the new node pool.
mark: nice, what about memory pressure under load?
jacob: still investigating, I will have numbers by Thursday.
mark: ok, let's action that.
jacob: I will also write up the benchmarking methodology so we can repeat it next quarter.
"""


async def main() -> None:
    from transform_service import db
    from transform_service.graph_builder import process_new_emails, process_new_events, process_new_transcripts
    from transform_service.models import RawEmail

    await db.create_staging_tables()
    pool = await db.get_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # Emails
        for subject, body, minutes_ago in (
            (STANDUP_SUBJECT, STANDUP_BODY, 5),
            (FOLLOWUP_SUBJECT, FOLLOWUP_BODY, 2),
        ):
            source_id = f"seed-{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO raw_emails (source_id, subject, from_email, to_emails, body, received_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (source_id) DO NOTHING
                """,
                source_id, subject, "shubham.gaur@onixnet.com", ["team@onixnet.com"], body,
                now - timedelta(minutes=minutes_ago),
            )
            print(f"seeded email source_id={source_id} subject={subject!r}")

        # Calendar events
        for title, desc, minutes_ago in (
            (PLANNING_TITLE, PLANNING_DESC, 20),
            (REVIEW_TITLE, REVIEW_DESC, 15),
            (ONE_ON_ONE_TITLE, ONE_ON_ONE_DESC, 10),
        ):
            source_id = f"seed-{uuid.uuid4().hex[:8]}"
            start = now - timedelta(minutes=minutes_ago)
            await conn.execute(
                """
                INSERT INTO raw_calendar_events (source_id, title, description, start_time, end_time)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (source_id) DO NOTHING
                """,
                source_id, title, desc, start, start + timedelta(minutes=30),
            )
            print(f"seeded event source_id={source_id} title={title!r}")

        # Meet transcript
        source_id = f"seed-{uuid.uuid4().hex[:8]}"
        await db.insert_meet_transcript(
            source_id=source_id, title=TRANSCRIPT_TITLE, transcript_text=TRANSCRIPT_TEXT,
        )
        print(f"seeded transcript source_id={source_id} title={TRANSCRIPT_TITLE!r}")

    print("\nrunning graph_builder.process_new_emails / process_new_events / process_new_transcripts ...")
    await process_new_emails()
    await process_new_events()
    await process_new_transcripts()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
