"""Reprocess all unprocessed rows from local Postgres."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()


async def backfill(
    source: str,
    limit: int,
    dry_run: bool,
    since: Optional[str],
) -> None:
    from transform_service import db

    await db.create_staging_tables()

    sources = []
    if source in ("EMAIL", "ALL"):
        sources.append("email")
    if source in ("CALENDAR", "ALL"):
        sources.append("calendar")
    if source in ("JIRA", "ALL"):
        sources.append("jira")

    for src in sources:
        print(f"\n--- Backfilling {src.upper()} ---")

        if src == "email":
            records = await db.get_unprocessed_emails(limit=limit)
        elif src == "calendar":
            records = await db.get_unprocessed_events(limit=limit)
        else:
            records = await db.get_unprocessed_jira_issues(limit=limit)

        if since:
            since_dt = datetime.fromisoformat(since)
            filtered = []
            for r in records:
                ts = getattr(r, "received_at", None) or getattr(r, "start_time", None) or getattr(r, "jira_created_at", None)
                if ts:
                    try:
                        record_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        if record_dt >= since_dt:
                            filtered.append(r)
                    except Exception:
                        filtered.append(r)
                else:
                    filtered.append(r)
            records = filtered

        print(f"  Found {len(records)} unprocessed record(s)")

        if dry_run:
            print("  DRY RUN — skipping processing")
            for r in records:
                sid = getattr(r, "source_id", getattr(r, "key", r.id))
                print(f"    would process: {sid}")
            continue

        try:
            from tqdm.asyncio import tqdm as atqdm
            use_tqdm = True
        except ImportError:
            use_tqdm = False

        processed = 0
        skipped = 0
        errors = 0

        iter_records = records
        if use_tqdm:
            iter_records = atqdm(records, desc=f"  {src}")

        for record in iter_records:
            try:
                if src == "email":
                    from transform_service.graph_builder import process_email
                    result = await process_email(record)
                elif src == "calendar":
                    from transform_service.graph_builder import process_calendar_event
                    result = await process_calendar_event(record)
                else:
                    from transform_service.jira_agent import sync_jira_issue
                    result = await sync_jira_issue(record)

                if result:
                    processed += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors += 1
                sid = getattr(record, "source_id", record.id)
                print(f"\n  ERROR processing {sid}: {exc}")

        print(f"\n  Summary: processed={processed}, skipped={skipped}, errors={errors}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill unprocessed records from Postgres")
    parser.add_argument(
        "--source",
        choices=["EMAIL", "CALENDAR", "JIRA", "ALL"],
        default="ALL",
        help="Which source to backfill",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max records per source")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without doing it")
    parser.add_argument("--since", type=str, default=None, help="Only process records after this date (YYYY-MM-DD)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(backfill(args.source, args.limit, args.dry_run, args.since))
