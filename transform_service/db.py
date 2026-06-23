from __future__ import annotations

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
    log.info("db.staging_tables_ready")


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
    pool = await get_pool()
    async with pool.acquire() as conn:
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, source_id, key, summary, status, assignee, priority,
                   jira_created_at::text, jira_updated_at::text, processed
            FROM raw_jira_issues
            WHERE processed = FALSE
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
        )
    return [
        RawJiraIssue(
            id=str(r["id"]),
            source_id=r["source_id"],
            key=r["key"],
            summary=r["summary"],
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
    allowed = {"raw_emails", "raw_calendar_events", "raw_jira_issues"}
    if table not in allowed:
        raise ValueError(f"Unknown table: {table}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE {table} SET processed = TRUE WHERE id = $1::uuid",
            record_id,
        )
