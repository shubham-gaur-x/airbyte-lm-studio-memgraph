"""
Memory retrieval — unified NL query interface over all memory layers.
Owns: full_memory_query, person_memory_profile.
All LM Studio calls reuse extractor._get_client() — no new AsyncOpenAI instances.
Do NOT call these from graph_builder.py — they are query-time, not ingestion-time.
MemorySession nodes are written via episodic_memory.log_memory_session() only.
"""
from __future__ import annotations

import json
import os
from typing import Any

import structlog

from transform_service import episodic_memory, memgraph_client
from transform_service.extractor import _get_client
from transform_service.utils import strip_json_fences

log = structlog.get_logger()

_ENTITY_SYSTEM = (
    'Extract entities from this question. Respond ONLY with JSON: '
    '{"people": ["name"], "topics": ["keyword"], "date_hint": "string or null"} '
    "Be conservative — only extract clearly named entities."
)

_SYNTHESIS_SYSTEM_PREFIX = (
    "You are a meeting memory assistant with access to a structured knowledge graph. "
    "Answer the question using ONLY the context below. "
    "Be specific and cite names and dates when available. "
    "If the context does not contain enough information, say so — do not guess.\n"
    "Context: "
)


async def _extract_entities(question: str) -> dict:
    """Short LM Studio call to extract structured entities from the question.
    On parse failure returns empty dict — does NOT raise."""
    client = _get_client()
    model = os.environ["LM_STUDIO_MODEL"]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ENTITY_SYSTEM},
                {"role": "user", "content": question},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        raw = strip_json_fences(resp.choices[0].message.content or "{}")
        parsed = json.loads(raw)
        return {
            "people": parsed.get("people") or [],
            "topics": parsed.get("topics") or [],
            "date_hint": parsed.get("date_hint"),
        }
    except Exception as exc:
        log.warning("memory_retrieval.entity_extract_failed", error=str(exc))
        return {"people": [], "topics": [], "date_hint": None}


async def _assemble_context(entities: dict) -> tuple[dict, list[str]]:
    """Query the graph for relevant nodes. Returns (context_dict, node_ids_used).
    Caps at 20 total nodes to avoid LM context overflow."""
    driver = memgraph_client.get_driver()
    node_ids: list[str] = []
    people_data: list[dict] = []
    topics_data: list[dict] = []
    total_nodes = 0

    # Fetch person subgraphs
    for name in (entities.get("people") or []):
        if total_nodes >= 20:
            break
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Person)
                WHERE toLower(p.name) CONTAINS toLower($name)
                OPTIONAL MATCH (p)-[:HAS_FACT]->(f:Fact)
                OPTIONAL MATCH (p)-[:PREFERS]->(pref:Preference)
                OPTIONAL MATCH (p)-[:ATTENDED]->(m:Meeting)
                WITH p,
                     collect(DISTINCT f.text)[..5] AS facts,
                     collect(DISTINCT pref.value)[..3] AS prefs,
                     collect(DISTINCT m) AS meetings
                RETURN p.id AS id, p.name AS name, p.email AS email,
                       p.pagerank_score AS pagerank_score,
                       p.community_id AS community_id,
                       facts, prefs, meetings
                LIMIT 3
                """,
                name=name,
            )
            # Memgraph doesn't support ORDER BY inside collect() — sort in Python instead
            async for r in result:
                row = dict(r)
                node_ids.append(row["id"])
                meeting_list = []
                for m in (row.get("meetings") or []):
                    if m is not None and hasattr(m, "items"):
                        meeting_list.append(dict(m))
                meeting_list.sort(key=lambda m: m.get("relevance_weight") or 0, reverse=True)
                meeting_list = meeting_list[:3]
                for mdict in meeting_list:
                    node_ids.append(mdict.get("id", ""))
                people_data.append({
                    "name": row["name"],
                    "email": row["email"],
                    "pagerank_score": row["pagerank_score"],
                    "community_id": row["community_id"],
                    "facts": row.get("facts") or [],
                    "preferences": row.get("prefs") or [],
                    "recent_meetings": meeting_list,
                })
                total_nodes += 1 + len(meeting_list)

    # Fetch topic subgraphs
    for topic in (entities.get("topics") or []):
        if total_nodes >= 20:
            break
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (t:Topic)
                WHERE toLower(t.name) CONTAINS toLower($topic)
                OPTIONAL MATCH (m:Meeting)-[:DISCUSSED]->(t)
                WITH t, collect(m) AS meetings
                RETURN t.id AS id, t.name AS name, meetings
                LIMIT 3
                """,
                topic=topic,
            )
            # Memgraph doesn't support ORDER BY inside collect() — sort in Python instead
            async for r in result:
                row = dict(r)
                node_ids.append(row["id"])
                meeting_list = []
                for m in (row.get("meetings") or []):
                    if m is not None and hasattr(m, "items"):
                        meeting_list.append(dict(m))
                meeting_list.sort(key=lambda m: m.get("relevance_weight") or 0, reverse=True)
                meeting_list = meeting_list[:3]
                for mdict in meeting_list:
                    node_ids.append(mdict.get("id", ""))
                topics_data.append({
                    "name": row["name"],
                    "recent_meetings": meeting_list,
                })
                total_nodes += 1 + len(meeting_list)

    # Build summary of algorithm scores for context
    algorithm_summary = ""
    if people_data:
        top = max(people_data, key=lambda p: p.get("pagerank_score") or 0)
        algorithm_summary = (
            f"Top person by PageRank: {top['name']}. "
            f"Found {len(people_data)} person(s) and {len(topics_data)} topic(s)."
        )

    context: dict[str, Any] = {
        "people": people_data,
        "topics": topics_data,
        "algorithm_summary": algorithm_summary,
    }
    return context, [nid for nid in node_ids if nid]


async def _synthesize(question: str, context: dict) -> str:
    """Call LM Studio with assembled graph context. Returns the model's answer.
    On API error returns a graceful fallback message — does NOT raise."""
    client = _get_client()
    model = os.environ["LM_STUDIO_MODEL"]

    system_prompt = _SYNTHESIS_SYSTEM_PREFIX + json.dumps(context, default=str, indent=2)

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        return resp.choices[0].message.content or "No answer generated."
    except Exception as exc:
        log.warning("memory_retrieval.synthesis_failed", error=str(exc))
        return "Unable to generate an answer at this time — the LM Studio model may be unavailable."


async def full_memory_query(question: str) -> dict:
    """Orchestrate entity extraction → context assembly → LM synthesis.
    Always returns a dict even on partial failure. After synthesis, logs
    a MemorySession via episodic_memory.log_memory_session()."""
    entities = await _extract_entities(question)
    context, node_ids = await _assemble_context(entities)
    answer = await _synthesize(question, context)

    session_id = await episodic_memory.log_memory_session(question, answer, node_ids)

    return {
        "answer": answer,
        "session_id": session_id,
        "nodes_used": [{"id": nid} for nid in node_ids],
        "context_summary": {
            "people_found": len(context.get("people") or []),
            "topics_found": len(context.get("topics") or []),
        },
    }


async def person_memory_profile(email: str) -> dict:
    """Return the full memory profile for one person.
    All from Cypher — no LM call. Returns {} if person not found."""
    driver = memgraph_client.get_driver()

    async with driver.session() as session:
        # Base person + algorithm scores
        result = await session.run(
            """
            MATCH (p:Person {email: $email})
            RETURN p.id AS id, p.name AS name, p.email AS email,
                   p.pagerank_score AS pagerank_score,
                   p.community_id AS community_id,
                   p.betweenness_centrality AS betweenness_centrality,
                   p.degree_centrality AS degree_centrality,
                   p.wcc_id AS wcc_id
            """,
            email=email,
        )
        records = [dict(r) async for r in result]
        if not records:
            return {}
        profile = records[0]

        # Semantic: facts. B2 (P4 extension): floor out low-confidence, single-mention
        # facts by default — semantic_memory seeds a new Fact at 0.3 and only raises it
        # +0.1 per repeat mention, so an unfiltered read surfaces a lot of one-off noise
        # at the same weight as a fact repeated across many meetings. Read-time (not
        # write-time, unlike ActionItem/Decision) since a Fact has no Jira-ticket-style
        # side effect to gate — nothing to block, just a retrieval-quality floor.
        min_confidence = float(os.environ.get("FACT_MIN_CONFIDENCE", "0.5"))
        result = await session.run(
            """
            MATCH (p:Person {email: $email})-[:ATTENDED]->(m:Meeting)-[:HAS_FACT]->(f:Fact)
            WHERE f.confidence >= $min_confidence
            RETURN DISTINCT f.id AS id, f.text AS text,
                            f.confidence AS confidence, f.source_count AS source_count
            ORDER BY f.confidence DESC
            LIMIT 10
            """,
            email=email,
            min_confidence=min_confidence,
        )
        profile["facts"] = [dict(r) async for r in result]

        # Semantic: preferences
        result = await session.run(
            """
            MATCH (p:Person {email: $email})-[:PREFERS]->(pref:Preference)
            RETURN pref.id AS id, pref.category AS category,
                   pref.value AS value, pref.confidence AS confidence
            ORDER BY pref.confidence DESC
            """,
            email=email,
        )
        profile["preferences"] = [dict(r) async for r in result]

        # Semantic: KNOWS connections. KNOWS is stored in one canonical direction
        # (lexicographically ordered emails), so match either direction here.
        result = await session.run(
            """
            MATCH (p:Person {email: $email})-[k:KNOWS]-(other:Person)
            RETURN other.name AS name, other.email AS email, k.weight AS weight
            ORDER BY k.weight DESC
            LIMIT 10
            """,
            email=email,
        )
        profile["knows"] = [dict(r) async for r in result]

        # Episodic: last 10 meetings with relevance_weight
        result = await session.run(
            """
            MATCH (p:Person {email: $email})-[:ATTENDED]->(m:Meeting)
            RETURN m.id AS id, m.title AS title, m.date AS date,
                   m.kind AS kind, m.relevance_weight AS relevance_weight
            ORDER BY m.relevance_weight DESC
            LIMIT 10
            """,
            email=email,
        )
        profile["recent_meetings"] = [dict(r) async for r in result]

        # Episodic: PRECEDED_BY chain depth (how long is the chain from their most recent meeting)
        result = await session.run(
            """
            MATCH (p:Person {email: $email})-[:ATTENDED]->(m:Meeting)
            WITH m ORDER BY m.date DESC LIMIT 1
            MATCH path = (m)-[:PRECEDED_BY*]->(oldest:Meeting)
            RETURN size(path) AS chain_depth
            ORDER BY chain_depth DESC
            LIMIT 1
            """,
            email=email,
        )
        chain_record = await result.single()
        profile["episodic_chain_depth"] = chain_record["chain_depth"] if chain_record else 0

        # Procedural: procedures this person's meetings follow
        result = await session.run(
            """
            MATCH (p:Person {email: $email})-[:ATTENDED]->(m:Meeting)-[:FOLLOWS_PROCEDURE]->(proc:Procedure)
            RETURN DISTINCT proc.name AS name, proc.is_inferred AS is_inferred,
                            proc.occurrence_count AS occurrence_count
            ORDER BY proc.occurrence_count DESC
            """,
            email=email,
        )
        profile["procedures"] = [dict(r) async for r in result]

    return profile
