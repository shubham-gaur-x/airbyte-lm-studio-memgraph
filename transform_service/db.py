from __future__ import annotations

import json
import os
from typing import List, Optional

import asyncpg
import structlog

from transform_service.models import RawCalendarEvent, RawEmail, RawJiraIssue

_pool: Optional[asyncpg.Pool] = None
log = structlog.get_logger()


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = (
            f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
            f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
        )
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        log.info("db.pool_created", dsn=dsn.split("@")[1])
    return _pool


async def create_staging_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Our manual staging tables (used for test inserts and smoke tests)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_emails (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_id TEXT UNIQUE NOT NULL,
                subject TEXT NOT NULL,
                from_email TEXT NOT NULL,
                to_emails TEXT[] NOT NULL DEFAULT '{}',
                body TEXT NOT NULL DEFAULT '',
                received_at TIMESTAMPTZ,
                processed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_calendar_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                start_time TIMESTAMPTZ,
                end_time TIMESTAMPTZ,
                attendees_json JSONB,
                processed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_jira_issues (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_id TEXT UNIQUE NOT NULL,
                key VARCHAR(50) NOT NULL,
                summary TEXT NOT NULL,
                status VARCHAR(50) NOT NULL,
                assignee VARCHAR(255),
                priority VARCHAR(50),
                jira_created_at TIMESTAMPTZ,
                jira_updated_at TIMESTAMPTZ,
                processed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Add processed flag to Airbyte's GCal table if it exists
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_name = 'raw_gcal_events') THEN
                    ALTER TABLE raw_gcal_events
                        ADD COLUMN IF NOT EXISTS processed BOOLEAN NOT NULL DEFAULT FALSE;
                END IF;
            END $$;
        """)
        # Add processed flag to Airbyte's Jira staging table (hash-suffixed name)
        airbyte_jira = await conn.fetchval("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name LIKE 'publicraw_jira_issues%'
            ORDER BY table_name LIMIT 1
        """)
        if airbyte_jira:
            await conn.execute(
                f'ALTER TABLE "{airbyte_jira}" ADD COLUMN IF NOT EXISTS processed BOOLEAN DEFAULT FALSE'
            )

    log.info("db.staging_tables_ready")


async def sync_airbyte_jira_to_staging() -> int:
    """No-op: Airbyte writes directly to raw_jira_issues. We query it natively."""
    return 0


async def get_unprocessed_emails(limit: int = 50) -> List[RawEmail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, source_id, subject, from_email, to_emails,
                   body, received_at::text, processed
            FROM raw_emails
            WHERE processed = FALSE
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
        )
    return [
        RawEmail(
            id=str(r["id"]),
            source_id=r["source_id"],
            subject=r["subject"],
            from_email=r["from_email"],
            to_emails=list(r["to_emails"]),
            body=r["body"],
            received_at=r["received_at"] or "",
            processed=r["processed"],
        )
        for r in rows
    ]


async def get_unprocessed_events(limit: int = 50) -> List[RawCalendarEvent]:
    """Read from Airbyte's raw_gcal_events table (preferred) with fallback to raw_calendar_events."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check if Airbyte's GCal table exists and has unprocessed rows
        gcal_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'raw_gcal_events'
                  AND table_schema = 'public'
            )
        """)

        if gcal_exists:
            rows = await conn.fetch(
                """
                SELECT
                    _airbyte_raw_id AS id,
                    id AS source_id,
                    COALESCE(summary, '(no title)') AS title,
                    description,
                    start->>'dateTime' AS start_time,
                    "end"->>'dateTime' AS end_time,
                    attendees::text AS attendees_json,
                    COALESCE(processed, FALSE) AS processed
                FROM raw_gcal_events
                WHERE COALESCE(processed, FALSE) = FALSE
                  AND status != 'cancelled'
                  AND start->>'dateTime' IS NOT NULL
                ORDER BY _airbyte_extracted_at ASC
                LIMIT $1
                """,
                limit,
            )
            return [
                RawCalendarEvent(
                    id=str(r["id"]),
                    source_id=r["source_id"],
                    title=r["title"],
                    description=r["description"],
                    start_time=r["start_time"] or "",
                    end_time=r["end_time"] or "",
                    attendees_json=r["attendees_json"],
                    processed=r["processed"],
                    source_table="raw_gcal_events",
                )
                for r in rows
            ]

        # Fallback: our manual staging table
        rows = await conn.fetch(
            """
            SELECT id::text, source_id, title, description,
                   start_time::text, end_time::text, attendees_json::text, processed
            FROM raw_calendar_events
            WHERE processed = FALSE
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [
            RawCalendarEvent(
                id=str(r["id"]),
                source_id=r["source_id"],
                title=r["title"],
                description=r["description"],
                start_time=r["start_time"] or "",
                end_time=r["end_time"] or "",
                attendees_json=r["attendees_json"],
                processed=r["processed"],
            )
            for r in rows
        ]


async def get_unprocessed_jira_issues(limit: int = 100) -> List[RawJiraIssue]:
    """Read from Airbyte's Jira staging table, de-duplicated by Jira issue id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        airbyte_table = await conn.fetchval("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name LIKE 'publicraw_jira_issues%'
            ORDER BY table_name LIMIT 1
        """)
        if not airbyte_table:
            return []

        rows = await conn.fetch(
            f"""
            SELECT DISTINCT ON (id)
                _airbyte_raw_id AS row_id,
                id::text AS jira_id,
                key,
                fields->>'summary' AS summary,
                COALESCE(fields->'status'->>'name', 'Unknown') AS status,
                fields->'assignee'->>'displayName' AS assignee,
                fields->'priority'->>'name' AS priority,
                created::text AS jira_created_at,
                updated::text AS jira_updated_at,
                COALESCE(processed, FALSE) AS processed
            FROM "{airbyte_table}"
            WHERE COALESCE(processed, FALSE) = FALSE
              AND key IS NOT NULL
              AND fields->>'summary' IS NOT NULL
            ORDER BY id, _airbyte_extracted_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [
        RawJiraIssue(
            id=str(r["row_id"]),
            source_id=str(r["jira_id"]),
            key=r["key"],
            summary=r["summary"] or "",
            status=r["status"],
            assignee=r["assignee"],
            priority=r["priority"],
            jira_created_at=r["jira_created_at"],
            jira_updated_at=r["jira_updated_at"],
            processed=r["processed"],
        )
        for r in rows
    ]


async def mark_processed(table: str, record_id: str) -> None:
    allowed = {"raw_emails", "raw_calendar_events", "raw_jira_issues", "raw_gcal_events"}
    if table not in allowed:
        raise ValueError(f"Unknown table: {table}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        if table == "raw_gcal_events":
            # uses varchar _airbyte_raw_id
            await conn.execute(
                "UPDATE raw_gcal_events SET processed = TRUE WHERE _airbyte_raw_id = $1",
                record_id,
            )
        elif table == "raw_jira_issues":
            # Mark processed by _airbyte_raw_id in Airbyte's hash-suffixed staging table
            airbyte_table = await conn.fetchval("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name LIKE 'publicraw_jira_issues%'
                ORDER BY table_name LIMIT 1
            """)
            if airbyte_table:
                await conn.execute(
                    f'UPDATE "{airbyte_table}" SET processed = TRUE WHERE _airbyte_raw_id = $1',
                    record_id,
                )
        else:
            await conn.execute(
                f"UPDATE {table} SET processed = TRUE WHERE id = $1::uuid",
                record_id,
            )
