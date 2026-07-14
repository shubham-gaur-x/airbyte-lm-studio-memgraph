"""
Episodic memory — temporal chains, causality, relevance decay, MemorySession.
Owns: PRECEDED_BY, CAUSED_BY, ACCESSED edges; MemorySession nodes; relevance_weight decay.
No MAGE CALL procedures here. All LM Studio calls reuse extractor._get_client().
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import structlog

from transform_service import memgraph_client
from transform_service.extractor import _get_client
from transform_service.models import ExtractedMeeting
from transform_service.utils import strip_json_fences, uuid5_id

log = structlog.get_logger()

_CAUSALITY_SYSTEM = """You are analyzing whether a meeting was caused by a prior \
decision or event. Given a meeting summary, identify if it explicitly continues \
or responds to a prior decision. Respond ONLY with JSON:
{"references_prior": true/false, "reference_description": "one sentence or null"}"""


async def link_temporal_chain(
    meeting_id: str, meeting_date: str, attendee_emails: list[str]
) -> bool:
    """Find the most recent previous meeting sharing >=1 attendee email and MERGE
    a PRECEDED_BY edge. Pure Cypher, no LM call. Returns True if a link was created."""
    if not attendee_emails:
        return False

    driver = memgraph_client.get_driver()
    now = datetime.now(timezone.utc).isoformat()

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (current:Meeting {id: $meeting_id})
            MATCH (prev:Person)-[:ATTENDED]->(prior:Meeting)
            WHERE prev.email IN $emails
              AND prior.date < $meeting_date
              AND prior.id <> $meeting_id
            WITH current, prior ORDER BY prior.date DESC LIMIT 1
            MERGE (current)-[p:PRECEDED_BY]->(prior)
            ON CREATE SET p.gap_days = (date($meeting_date) - date(prior.date)).day,
                          p.created_at = $now
            RETURN prior.id AS prior_id
            """,
            meeting_id=meeting_id,
            emails=attendee_emails,
            meeting_date=meeting_date,
            now=now,
        )
        record = await result.single()

    linked = record is not None
    if linked:
        log.info(
            "episodic_memory.temporal_chain_linked",
            meeting_id=meeting_id,
            prior_id=record["prior_id"],  # type: ignore[index]  # guarded by `if linked` above
        )
    return linked


async def detect_causality(meeting: ExtractedMeeting, meeting_id: str) -> int:
    """Only runs when meeting.follow_up_needed is True.
    Asks LM Studio if this meeting references a prior decision.
    If yes, finds the matching Decision or Meeting node and MERGEs CAUSED_BY.
    Returns count of causal links created (capped at 1 per meeting)."""
    if not meeting.follow_up_needed:
        return 0

    import os

    client = _get_client()
    model = os.environ["LM_STUDIO_MODEL"]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CAUSALITY_SYSTEM},
                {"role": "user", "content": meeting.summary},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        raw = strip_json_fences(resp.choices[0].message.content or "{}")
        parsed = json.loads(raw)
        references_prior: bool = bool(parsed.get("references_prior", False))
        ref_desc: Optional[str] = parsed.get("reference_description") or None
    except Exception as exc:
        log.warning("episodic_memory.causality_lm_failed", meeting_id=meeting_id, error=str(exc))
        return 0

    if not references_prior or not ref_desc:
        return 0

    # Search for a Decision node with >50% word overlap with the reference description
    ref_words = set(ref_desc.lower().split())
    driver = memgraph_client.get_driver()

    async with driver.session() as session:
        # Try Decision nodes first
        result = await session.run(
            """
            MATCH (d:Decision)
            RETURN d.id AS id, d.text AS text, 'Decision' AS type
            LIMIT 50
            """
        )
        candidates = [dict(r) async for r in result]

    best_id: Optional[str] = None
    best_type: Optional[str] = None
    best_overlap = 0.0

    for cand in candidates:
        if not cand.get("text"):
            continue
        cand_words = set(cand["text"].lower().split())
        if not cand_words:
            continue
        overlap = len(ref_words & cand_words) / len(ref_words | cand_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_id = cand["id"]
            best_type = cand["type"]

    # Fall back to Meeting nodes by title similarity if no good Decision match
    if best_overlap < 0.5:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (m:Meeting)
                WHERE m.id <> $meeting_id
                RETURN m.id AS id, m.title AS text, 'Meeting' AS type
                LIMIT 50
                """,
                meeting_id=meeting_id,
            )
            meeting_candidates = [dict(r) async for r in result]

        for cand in meeting_candidates:
            if not cand.get("text"):
                continue
            cand_words = set(cand["text"].lower().split())
            if not cand_words:
                continue
            overlap = len(ref_words & cand_words) / len(ref_words | cand_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_id = cand["id"]
                best_type = cand["type"]

    if best_overlap < 0.5 or best_id is None:
        log.info("episodic_memory.causality_no_match", meeting_id=meeting_id, overlap=best_overlap)
        return 0

    # MERGE the CAUSED_BY edge (match cause by id regardless of label)
    async with driver.session() as session:
        await session.run(
            """
            MATCH (m:Meeting {id: $meeting_id})
            MATCH (cause {id: $cause_id})
            MERGE (m)-[:CAUSED_BY {confidence: 0.7}]->(cause)
            """,
            meeting_id=meeting_id,
            cause_id=best_id,
        )

    log.info(
        "episodic_memory.causality_linked",
        meeting_id=meeting_id,
        cause_id=best_id,
        cause_type=best_type,
        overlap=round(best_overlap, 2),
    )
    return 1


async def decay_relevance() -> dict:
    """Nightly: decay Meeting.relevance_weight by 5% per day, floor 0.1."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting)
            SET m.relevance_weight = CASE
                WHEN m.relevance_weight IS NULL THEN 1.0
                WHEN m.relevance_weight <= 0.1 THEN 0.1
                ELSE m.relevance_weight * 0.95
            END
            RETURN count(m) AS updated
            """
        )
        record = await result.single()
        updated = record["updated"] if record else 0

    log.info("episodic_memory.decay_done", meetings_decayed=updated)
    return {"meetings_decayed": updated}


async def log_memory_session(
    query_text: str, answer_text: str, node_ids: list[str]
) -> str:
    """Create a MemorySession node and ACCESSED edges to every contributing node.
    Returns the session_id. Called by memory_retrieval.py only."""
    now = datetime.now(timezone.utc).isoformat()
    session_id = uuid5_id("session", f"{now}{query_text}")

    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MERGE (ms:MemorySession {id: $session_id})
            SET ms.query_text = $query_text,
                ms.answer_text = $answer_text,
                ms.nodes_accessed = $node_count,
                ms.created_at = $now
            """,
            session_id=session_id,
            query_text=query_text,
            answer_text=answer_text,
            node_count=len(node_ids),
            now=now,
        )
        for node_id in node_ids:
            await session.run(
                """
                MATCH (ms:MemorySession {id: $session_id})
                MATCH (n {id: $node_id})
                MERGE (ms)-[:ACCESSED]->(n)
                """,
                session_id=session_id,
                node_id=node_id,
            )

    log.info(
        "episodic_memory.session_logged",
        session_id=session_id,
        nodes=len(node_ids),
    )
    return session_id
