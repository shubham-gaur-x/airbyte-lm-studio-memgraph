"""Database layer for dev_agent — owns its own asyncpg pool and dev_agent_runs table."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

import asyncpg
import structlog

from dev_agent.models import DevAgentRun

log = structlog.get_logger()

_pool: Optional[asyncpg.Pool] = None


def _dsn() -> str:
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_HOST']}:{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ['POSTGRES_DB']}"
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=5)
    return _pool


async def ensure_table() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS dev_agent_runs (
                ticket_key    TEXT PRIMARY KEY,
                status        TEXT NOT NULL,
                branch_name   TEXT,
                pr_url        TEXT,
                pr_number     INTEGER,
                error         TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                started_at    TIMESTAMPTZ,
                finished_at   TIMESTAMPTZ,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    log.info("dev_agent.db.table_ensured")


async def get_run(ticket_key: str) -> Optional[DevAgentRun]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM dev_agent_runs WHERE ticket_key = $1", ticket_key
        )
    if row is None:
        return None
    return DevAgentRun(**dict(row))


async def should_attempt(ticket_key: str, max_attempts: int) -> bool:
    run = await get_run(ticket_key)
    if run is None:
        return True
    if run.status in ("running", "pr_opened"):
        return False
    if run.status == "failed":
        return run.attempt_count < max_attempts
    return True


async def start_run(ticket_key: str, branch_name: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO dev_agent_runs (ticket_key, status, branch_name, attempt_count, started_at)
            VALUES ($1, 'running', $2, 1, NOW())
            ON CONFLICT (ticket_key) DO UPDATE
            SET status       = 'running',
                branch_name  = EXCLUDED.branch_name,
                attempt_count = dev_agent_runs.attempt_count + 1,
                started_at   = NOW(),
                finished_at  = NULL,
                error        = NULL
            """,
            ticket_key,
            branch_name,
        )


async def finish_run(
    ticket_key: str,
    status: str,
    pr_url: Optional[str] = None,
    pr_number: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE dev_agent_runs
            SET status      = $2,
                pr_url      = $3,
                pr_number   = $4,
                error       = $5,
                finished_at = NOW()
            WHERE ticket_key = $1
            """,
            ticket_key,
            status,
            pr_url,
            pr_number,
            error,
        )


async def list_recent_runs(limit: int = 50) -> List[DevAgentRun]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM dev_agent_runs ORDER BY created_at DESC LIMIT $1", limit
        )
    return [DevAgentRun(**dict(r)) for r in rows]
