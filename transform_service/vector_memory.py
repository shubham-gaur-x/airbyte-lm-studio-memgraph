"""
Vector memory — semantic (embedding-based) search over Meeting and Fact nodes.
Owns the `embedding` property on those node types (same pattern as
graph_algorithms.py writing pagerank_score etc onto nodes it doesn't otherwise own).

MAGE vector_search CALL procedures are never issued from this module directly —
graph_algorithms.py is the only place CALL procedures appear. This module calls
graph_algorithms.vector_search() instead.

Embeddings are generated via LM Studio's /v1/embeddings endpoint, reusing the
existing extractor._get_client() singleton — no new AsyncOpenAI instance.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import structlog

from transform_service import graph_algorithms, memgraph_client
from transform_service.extractor import _get_client

log = structlog.get_logger()


async def embed_text(text: str) -> list[float] | None:
    """Call LM Studio's embedding model. Returns None on failure — never raises,
    embedding is an enrichment step and must not block the ingestion pipeline."""
    if not text or not text.strip():
        return None

    client = _get_client()
    model = os.environ.get("LM_STUDIO_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5")

    try:
        resp = await client.embeddings.create(model=model, input=text)
        return resp.data[0].embedding
    except Exception as exc:
        log.warning("vector_memory.embed_failed", error=str(exc))
        return None


async def embed_meeting(meeting_id: str, summary: str) -> bool:
    """Embed a meeting's summary and write it to Meeting.embedding.
    Returns True if the embedding was written."""
    vector = await embed_text(summary)
    if vector is None:
        return False

    now = datetime.now(timezone.utc).isoformat()
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (m:Meeting {id: $meeting_id})
            SET m.embedding = $embedding, m.embedding_updated_at = $now
            """,
            meeting_id=meeting_id,
            embedding=vector,
            now=now,
        )

    log.info("vector_memory.meeting_embedded", meeting_id=meeting_id)
    return True


async def embed_facts_for_meeting(meeting_id: str) -> int:
    """Embed any Facts attached to this meeting that don't have an embedding yet.
    Idempotent and safe to call on every ingestion — MERGE-matched existing Facts
    from earlier meetings are only embedded once. Returns count embedded."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting {id: $meeting_id})-[:HAS_FACT]->(f:Fact)
            WHERE f.embedding IS NULL
            RETURN f.id AS id, f.text AS text
            """,
            meeting_id=meeting_id,
        )
        pending = [dict(r) async for r in result]

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for fact in pending:
        vector = await embed_text(fact["text"])
        if vector is None:
            continue
        async with driver.session() as session:
            await session.run(
                """
                MATCH (f:Fact {id: $id})
                SET f.embedding = $embedding, f.embedding_updated_at = $now
                """,
                id=fact["id"],
                embedding=vector,
                now=now,
            )
        count += 1

    if count:
        log.info("vector_memory.facts_embedded", meeting_id=meeting_id, count=count)
    return count


async def search_similar_meetings(query_text: str, limit: int = 5) -> list[dict]:
    """Semantic search over Meeting.summary embeddings. Returns meetings ranked
    by similarity, richest fields first. Empty list on embedding failure."""
    vector = await embed_text(query_text)
    if vector is None:
        return []

    hits = await graph_algorithms.vector_search("meeting_embedding_idx", vector, limit)
    if not hits:
        return []

    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            UNWIND $ids AS mid
            MATCH (m:Meeting {id: mid})
            RETURN m.id AS id, m.title AS title, m.date AS date,
                   m.summary AS summary, m.kind AS kind
            """,
            ids=[h["node_id"] for h in hits],
        )
        meetings_by_id = {r["id"]: dict(r) async for r in result}

    enriched = []
    for hit in hits:
        meeting = meetings_by_id.get(hit["node_id"])
        if meeting is None:
            continue
        enriched.append({**meeting, "similarity": hit["similarity"]})
    return enriched


async def search_similar_facts(query_text: str, limit: int = 5) -> list[dict]:
    """Semantic search over Fact.text embeddings. Returns facts ranked by
    similarity. Empty list on embedding failure."""
    vector = await embed_text(query_text)
    if vector is None:
        return []

    hits = await graph_algorithms.vector_search("fact_embedding_idx", vector, limit)
    if not hits:
        return []

    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            UNWIND $ids AS fid
            MATCH (f:Fact {id: fid})
            RETURN f.id AS id, f.text AS text, f.confidence AS confidence,
                   f.source_count AS source_count
            """,
            ids=[h["node_id"] for h in hits],
        )
        facts_by_id = {r["id"]: dict(r) async for r in result}

    enriched = []
    for hit in hits:
        fact = facts_by_id.get(hit["node_id"])
        if fact is None:
            continue
        enriched.append({**fact, "similarity": hit["similarity"]})
    return enriched


async def backfill_embeddings() -> dict:
    """One-off/maintenance: embed any Meeting or Fact nodes created before this
    module existed (or that failed embedding earlier). Safe to re-run — only
    processes nodes missing an embedding. Not on the nightly scheduler; run
    manually after deploying this feature or after a bulk data import."""
    driver = memgraph_client.get_driver()

    async with driver.session() as session:
        result = await session.run(
            "MATCH (m:Meeting) WHERE m.embedding IS NULL AND m.summary IS NOT NULL "
            "RETURN m.id AS id, m.summary AS text"
        )
        pending_meetings = [dict(r) async for r in result]

        result = await session.run(
            "MATCH (f:Fact) WHERE f.embedding IS NULL RETURN f.id AS id, f.text AS text"
        )
        pending_facts = [dict(r) async for r in result]

    meetings_embedded = 0
    for m in pending_meetings:
        if await embed_meeting(m["id"], m["text"]):
            meetings_embedded += 1

    now = datetime.now(timezone.utc).isoformat()
    facts_embedded = 0
    for f in pending_facts:
        vector = await embed_text(f["text"])
        if vector is None:
            continue
        async with driver.session() as session:
            await session.run(
                "MATCH (f:Fact {id: $id}) SET f.embedding = $embedding, f.embedding_updated_at = $now",
                id=f["id"],
                embedding=vector,
                now=now,
            )
        facts_embedded += 1

    summary = {"meetings_embedded": meetings_embedded, "facts_embedded": facts_embedded}
    log.info("vector_memory.backfill_done", **summary)
    return summary
