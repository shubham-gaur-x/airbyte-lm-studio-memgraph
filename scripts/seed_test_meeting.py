"""E2E test seed: insert a realistic backend-sync meeting and run the real pipeline.

Inserts one email into raw_emails, then runs graph_builder.process_new_emails so the
full local path executes: classify -> extract (LM Studio gemma) -> Memgraph write ->
jira_pusher (creates a real Jira ticket for the engineering action item).

Prints the source_id so the caller can query Memgraph for the created ActionItem/jira_key.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

SUBJECT = "Backend Sync — Jira read-back bug + v5 provenance decisions"

BODY = """\
Hi all,

Date: Tuesday, 28 July 2026, 10:00–10:45 (Google Meet).

Recap of today's backend sync with Matteo (matteo@onixnet.com),
Priya Nair (priya.nair@onixnet.com), Tom Alvarez (tom.alvarez@onixnet.com) and myself
(shubham.gaur@onixnet.com).

Topics discussed:
- Reliability of the bidirectional Jira read-back into Memgraph
- Graph provenance for the v5 upgrade
- Per-item confidence gating before we create Jira tickets

Key decisions:
- We decided the bidirectional Jira sync must report real match counts before any v5
  provenance work starts — right now the numbers are not trustworthy.
- We agreed that in v5, Jira ticket creation will be gated on per-item confidence, not
  just the meeting-action-item label.

Action items:
- Shubham to fix the Jira read-back bug in jira_agent.sync_jira_issue: it currently
  returns True for every issue even when no ActionItem node matches in Memgraph, so the
  matched/unmatched counters in process_jira_issues are meaningless. Make
  update_action_jira_status report whether it actually matched an ActionItem node, have
  sync_jira_issue return that real boolean, and add a test covering matched and unmatched
  cases. This is an engineering/code change. Due next Friday.
- Matteo to schedule a follow-up review with the data team by end of week.

Thanks,
Shubham
"""


async def main() -> None:
    from transform_service import db
    from transform_service.graph_builder import process_email
    from transform_service.models import RawEmail

    await db.create_staging_tables()
    source_id = f"e2e-meeting-{uuid.uuid4().hex[:8]}"
    to_emails = ["matteo@onixnet.com", "priya.nair@onixnet.com", "tom.alvarez@onixnet.com"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row_id = await conn.fetchval(
            """
            INSERT INTO raw_emails (source_id, subject, from_email, to_emails, body)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            source_id,
            SUBJECT,
            "shubham.gaur@onixnet.com",
            to_emails,
            BODY,
        )
    print(f"SEEDED source_id={source_id} row_id={row_id}", flush=True)

    # Process ONLY this email (not the poller) so real Gmail data in messages_details
    # is never touched by this test.
    email = RawEmail(
        id=str(row_id),
        source_id=source_id,
        subject=SUBJECT,
        from_email="shubham.gaur@onixnet.com",
        to_emails=to_emails,
        body=BODY,
        received_at="",
        processed=False,
        source_table="raw_emails",
    )
    result = await process_email(email)
    print(f"PIPELINE_DONE source_id={source_id} processed={result}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
