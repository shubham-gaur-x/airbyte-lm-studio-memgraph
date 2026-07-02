from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from transform_service import db, episodic_memory, graph_algorithms, memgraph_client, procedural_memory, semantic_memory, vector_memory
from transform_service.memory_retrieval import full_memory_query, person_memory_profile
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
    scheduler.add_job(
        graph_algorithms.run_full_algorithms,
        "cron", hour=2, minute=0,
        id="nightly_algorithms",
    )
    scheduler.add_job(
        semantic_memory.consolidate_semantic,
        "cron", hour=2, minute=15,
        id="nightly_consolidate_semantic",
    )
    scheduler.add_job(
        episodic_memory.decay_relevance,
        "cron", hour=2, minute=30,
        id="nightly_decay",
    )
    scheduler.add_job(
        procedural_memory.discover_procedures,
        "cron", hour=2, minute=45,
        id="nightly_discover_procedures",
    )
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


@app.get("/graph/insights/influential")
async def insights_influential(label: str = "Person", limit: int = 10) -> Dict[str, Any]:
    nodes = await memgraph_client.get_influential_nodes(label=label, limit=limit)
    return {"label": label, "nodes": nodes, "count": len(nodes)}


@app.get("/graph/insights/communities")
async def insights_communities() -> Dict[str, Any]:
    communities = await memgraph_client.get_all_communities()
    return {"communities": communities, "count": len(communities)}


@app.get("/graph/insights/bridges")
async def insights_bridges(limit: int = 10) -> Dict[str, Any]:
    nodes = await memgraph_client.get_bridge_nodes(limit=limit)
    return {"nodes": nodes, "count": len(nodes)}


@app.get("/graph/insights/node/{node_id}")
async def insights_node(node_id: str) -> Dict[str, Any]:
    result = await memgraph_client.get_node_insights(node_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return result


@app.get("/graph/procedures")
async def list_procedures() -> Dict[str, Any]:
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (p:Procedure)
            OPTIONAL MATCH (p)-[:HAS_STEP]->(s:ProcedureStep)
            RETURN p.id AS id, p.name AS name, p.description AS description,
                   p.is_inferred AS is_inferred,
                   p.occurrence_count AS occurrence_count,
                   collect({order: s.order, name: s.name}) AS steps
            ORDER BY occurrence_count DESC
            """
        )
        procedures = [dict(r) async for r in result]
    return {"procedures": procedures, "count": len(procedures)}


@app.get("/graph/procedures/{procedure_name}")
async def get_procedure(procedure_name: str) -> Dict[str, Any]:
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        proc_result = await session.run(
            """
            MATCH (p:Procedure {name: $name})
            OPTIONAL MATCH (p)-[:HAS_STEP]->(s:ProcedureStep)
            RETURN p.id AS id, p.name AS name, p.description AS description,
                   p.is_inferred AS is_inferred,
                   p.occurrence_count AS occurrence_count,
                   collect({order: s.order, name: s.name}) AS steps
            """,
            name=procedure_name,
        )
        procs = [dict(r) async for r in proc_result]
        if not procs:
            raise HTTPException(status_code=404, detail=f"Procedure not found: {procedure_name}")
        proc = procs[0]

        meetings_result = await session.run(
            """
            MATCH (m:Meeting)-[:FOLLOWS_PROCEDURE]->(p:Procedure {name: $name})
            RETURN m.id AS id, m.title AS title, m.date AS date,
                   m.kind AS kind, m.relevance_weight AS relevance_weight
            ORDER BY m.date DESC
            LIMIT 20
            """,
            name=procedure_name,
        )
        proc["meetings"] = [dict(r) async for r in meetings_result]
    return proc


@app.post("/graph/memory/query")
async def memory_query(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must be a non-empty string")
    return await full_memory_query(question)


@app.get("/graph/memory/person/{email}")
async def memory_person(email: str) -> Dict[str, Any]:
    profile = await person_memory_profile(email)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Person not found: {email}")
    return profile


@app.get("/graph/memory/sessions")
async def memory_sessions() -> Dict[str, Any]:
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (ms:MemorySession)
            RETURN ms.id AS id, ms.query_text AS query_text,
                   ms.answer_text AS answer_text,
                   ms.nodes_accessed AS nodes_accessed,
                   ms.created_at AS created_at
            ORDER BY ms.created_at DESC
            LIMIT 20
            """
        )
        sessions_data = [dict(r) async for r in result]
    return {"sessions": sessions_data, "count": len(sessions_data)}


@app.get("/graph/search/meetings")
async def search_meetings(q: str, limit: int = 5) -> Dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must be a non-empty string")
    results = await vector_memory.search_similar_meetings(q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}


@app.get("/graph/search/facts")
async def search_facts(q: str, limit: int = 5) -> Dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must be a non-empty string")
    results = await vector_memory.search_similar_facts(q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}
