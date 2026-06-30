from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from transform_service import db, memgraph_client
from transform_service.digest import weekly_digest
from transform_service.graph_builder import process_new_emails, process_new_events
from transform_service.jira_agent import process_jira_issues
from transform_service.models import AirbyteWebhookPayload
from transform_service.utils import configure_logging

log = configure_logging()
scheduler = AsyncIOScheduler()


async def _ping_lm_studio() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{os.environ['LM_STUDIO_BASE_URL'].rstrip('/v1').rstrip('/')}/v1/models",
                headers={"Authorization": "Bearer lm-studio"},
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _ping_memgraph() -> bool:
    try:
        driver = memgraph_client.get_driver()
        async with driver.session() as session:
            await session.run("RETURN 1")
        return True
    except Exception:
        return False


async def _ping_postgres() -> bool:
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        lm_studio_url=os.environ.get("LM_STUDIO_BASE_URL"),
        memgraph_host=os.environ.get("MEMGRAPH_HOST"),
        jira_enabled=os.environ.get("JIRA_ENABLED"),
        mcp_url="http://memgraph-mcp:8000/mcp/",
    )

    await db.create_staging_tables()
    await memgraph_client.create_indexes()

    scheduler.add_job(db.create_staging_tables, "interval", minutes=5, id="ensure_columns")
    scheduler.add_job(process_new_emails, "interval", minutes=5, id="poll_emails")
    scheduler.add_job(process_new_events, "interval", minutes=5, id="poll_events")
    scheduler.add_job(process_jira_issues, "interval", minutes=5, id="poll_jira")
    scheduler.start()
    log.info("service.scheduler_started", interval_minutes=5)

    yield

    scheduler.shutdown(wait=False)
    await memgraph_client.close_driver()


app = FastAPI(title="meeting-memory transform service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.post("/webhook/airbyte")
async def webhook_airbyte(
    payload: AirbyteWebhookPayload, background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    if payload.status != "succeeded":
        log.info("webhook.ignored", status=payload.status, connection_id=payload.connection_id)
        return {"status": "ignored", "reason": f"status={payload.status}"}

    background_tasks.add_task(process_new_emails)
    background_tasks.add_task(process_new_events)
    background_tasks.add_task(process_jira_issues)

    log.info("webhook.queued", connection_id=payload.connection_id, job_id=payload.job_id)
    return {"status": "queued", "connection_id": payload.connection_id}


@app.get("/health")
async def health() -> Dict[str, Any]:
    lm_ok, mg_ok, pg_ok = await _ping_lm_studio(), await _ping_memgraph(), await _ping_postgres()
    status = "ok" if (lm_ok and mg_ok and pg_ok) else "degraded"
    return {
        "status": status,
        "lm_studio": lm_ok,
        "memgraph": mg_ok,
        "postgres": pg_ok,
    }


@app.get("/graph/meetings/recent")
async def meetings_recent() -> Dict[str, Any]:
    meetings = await memgraph_client.get_recent_meetings(limit=10)
    return {"meetings": meetings, "count": len(meetings)}


@app.get("/graph/person/{email}")
async def person(email: str) -> Dict[str, Any]:
    result = await memgraph_client.get_person_graph(email)
    if not result:
        raise HTTPException(status_code=404, detail=f"Person not found: {email}")
    return result


@app.get("/graph/topic/{name}")
async def topic(name: str) -> Dict[str, Any]:
    result = await memgraph_client.get_topic_graph(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Topic not found: {name}")
    return result


@app.get("/graph/actions/open")
async def actions_open() -> Dict[str, Any]:
    actions = await memgraph_client.get_open_actions()
    return {"actions": actions, "count": len(actions)}


@app.get("/graph/timeline")
async def timeline(window: str = "week") -> Dict[str, Any]:
    if window not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="window must be day, week, or month")
    return await memgraph_client.get_timeline(window)  # type: ignore[arg-type]


@app.get("/graph/digest/weekly")
async def digest_weekly() -> Dict[str, Any]:
    return await weekly_digest()
