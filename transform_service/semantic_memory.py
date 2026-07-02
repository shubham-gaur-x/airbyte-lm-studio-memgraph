"""
Semantic memory — Fact, Preference, KNOWS, INTERESTED_IN.
All Cypher lives here (or in memgraph_client.py). No MAGE CALL procedures.
All LM Studio calls reuse extractor._get_client() — no new AsyncOpenAI instances.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from transform_service import memgraph_client
from transform_service.extractor import _get_client
from transform_service.models import ExtractedMeeting
from transform_service.utils import strip_json_fences, uuid5_id

log = structlog.get_logger()

_FACT_SYSTEM = (
    "Extract 3-5 durable facts from this meeting summary. "
    "A fact is something persistently true about a person, project, or topic — "
    "not a one-time event. Respond ONLY with a JSON array of strings. "
    'Example: ["Alice leads the backend team", "The API migration is Q3"]'
)

_PREF_SYSTEM = (
    "Given this person's meeting history context, infer 1-2 preferences about how they work. "
    "Respond ONLY with a JSON array of objects: "
    '[{"category": "string", "value": "string"}]. '
    "Categories: communication_style, meeting_frequency, topic_interest, "
    "work_pattern, timezone_preference. Be specific, not generic."
)


async def extract_facts(meeting: ExtractedMeeting, meeting_id: str) -> int:
    """Call LM Studio to extract 3-5 durable facts from the meeting summary.
    MERGE Fact nodes with HAS_FACT edges to the Meeting. Returns count of facts written."""
    import os

    client = _get_client()
    model = os.environ["LM_STUDIO_MODEL"]
    now = datetime.now(timezone.utc).isoformat()

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _FACT_SYSTEM},
                {"role": "user", "content": meeting.summary},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        raw = strip_json_fences(resp.choices[0].message.content or "[]")
        facts: list[str] = json.loads(raw)
        if not isinstance(facts, list):
            raise ValueError("expected array")
    except Exception as exc:
        log.warning("semantic_memory.extract_facts_failed", meeting_id=meeting_id, error=str(exc))
        return 0

    driver = memgraph_client.get_driver()
    count = 0
    async with driver.session() as session:
        for fact_text in facts:
            if not isinstance(fact_text, str) or not fact_text.strip():
                continue
            fact_id = uuid5_id("fact", fact_text.lower().strip())
            try:
                await session.run(
                    """
                    MERGE (f:Fact {id: $fact_id})
                    ON CREATE SET f.text = $text,
                                  f.confidence = 0.3,
                                  f.source_count = 1,
                                  f.created_at = $now
                    ON MATCH SET  f.source_count = f.source_count + 1,
                                  f.confidence = CASE
                                      WHEN f.confidence + 0.1 > 1.0 THEN 1.0
                                      ELSE f.confidence + 0.1
                                  END,
                                  f.updated_at = $now
                    WITH f
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:HAS_FACT]->(f)
                    """,
                    fact_id=fact_id,
                    text=fact_text.strip(),
                    now=now,
                    meeting_id=meeting_id,
                )
                count += 1
            except Exception as exc:
                log.warning("semantic_memory.fact_write_failed", fact_id=fact_id, error=str(exc))

    log.info("semantic_memory.facts_extracted", meeting_id=meeting_id, count=count)
    return count


async def infer_preferences(meeting: ExtractedMeeting, meeting_id: str) -> int:
    """Infer preferences for attendees with >= 3 meetings in the graph.
    Returns total count of preferences written."""
    import os

    client = _get_client()
    model = os.environ["LM_STUDIO_MODEL"]
    now = datetime.now(timezone.utc).isoformat()
    driver = memgraph_client.get_driver()
    total = 0

    for attendee in meeting.attendees:
        if not attendee.email:
            continue

        # Check meeting count for this attendee
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Person {email: $email})-[:ATTENDED]->(m:Meeting)
                RETURN count(m) AS meeting_count
                """,
                email=attendee.email,
            )
            record = await result.single()
            meeting_count = record["meeting_count"] if record else 0

        if meeting_count < 3:
            continue

        user_content = (
            f"Person: {attendee.name}\n"
            f"Role: {attendee.role}\n"
            f"Meeting topics: {', '.join(meeting.topics)}\n"
            f"Meeting kind: {meeting.kind}"
        )

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _PREF_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=256,
            )
            raw = strip_json_fences(resp.choices[0].message.content or "[]")
            prefs: list[dict[str, Any]] = json.loads(raw)
            if not isinstance(prefs, list):
                raise ValueError("expected array")
        except Exception as exc:
            log.warning(
                "semantic_memory.infer_preferences_failed",
                email=attendee.email,
                error=str(exc),
            )
            continue

        async with driver.session() as session:
            for pref in prefs:
                if not isinstance(pref, dict):
                    continue
                category = pref.get("category", "").strip()
                value = pref.get("value", "").strip()
                if not category or not value:
                    continue

                pref_id = uuid5_id("preference", f"{attendee.email}:{category}:{value}")
                try:
                    await session.run(
                        """
                        MERGE (pref:Preference {id: $pref_id})
                        ON CREATE SET pref.category = $category,
                                      pref.value = $value,
                                      pref.confidence = 0.5,
                                      pref.created_at = $now
                        ON MATCH SET  pref.confidence = CASE
                                          WHEN pref.confidence + 0.1 > 1.0 THEN 1.0
                                          ELSE pref.confidence + 0.1
                                      END,
                                      pref.updated_at = $now
                        WITH pref
                        MATCH (p:Person {email: $email})
                        MERGE (p)-[:PREFERS]->(pref)
                        """,
                        pref_id=pref_id,
                        category=category,
                        value=value,
                        email=attendee.email,
                        now=now,
                    )
                    total += 1
                except Exception as exc:
                    log.warning(
                        "semantic_memory.pref_write_failed",
                        pref_id=pref_id,
                        error=str(exc),
                    )

    log.info("semantic_memory.preferences_inferred", meeting_id=meeting_id, total=total)
    return total


async def strengthen_relationships(meeting: ExtractedMeeting, meeting_id: str) -> None:
    """Pure Cypher — no LM call. Strengthen KNOWS and INTERESTED_IN edges."""
    now = datetime.now(timezone.utc).isoformat()
    emails = [a.email for a in meeting.attendees if a.email]
    if not emails:
        return

    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        # KNOWS: strengthen all co-attendee pairs
        await session.run(
            """
            UNWIND $emails AS email1
            UNWIND $emails AS email2
            WITH email1, email2 WHERE email1 < email2
            MATCH (p1:Person {email: email1}), (p2:Person {email: email2})
            MERGE (p1)-[k:KNOWS]->(p2)
            ON CREATE SET k.weight = 1, k.created_at = $now
            ON MATCH SET  k.weight = k.weight + 1, k.updated_at = $now
            """,
            emails=emails,
            now=now,
        )

        # INTERESTED_IN: strengthen person → topic weights
        for topic_name in meeting.topics:
            await session.run(
                """
                UNWIND $emails AS email
                MATCH (p:Person {email: email}), (t:Topic {name: $topic})
                MERGE (p)-[i:INTERESTED_IN]->(t)
                ON CREATE SET i.weight = 1, i.created_at = $now
                ON MATCH SET  i.weight = i.weight + 1, i.updated_at = $now
                """,
                emails=emails,
                topic=topic_name,
                now=now,
            )

    log.info(
        "semantic_memory.relationships_strengthened",
        meeting_id=meeting_id,
        pairs=len(emails) * (len(emails) - 1) // 2,
        topics=len(meeting.topics),
    )


async def consolidate_semantic() -> dict:
    """Nightly: raise confidence of well-confirmed facts.
    Facts whose source_count is a multiple of 3 get a 0.2 confidence boost."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (f:Fact)
            WHERE f.source_count % 3 = 0 AND f.confidence < 1.0
            SET f.confidence = CASE
                WHEN f.confidence + 0.2 > 1.0 THEN 1.0
                ELSE f.confidence + 0.2
            END
            RETURN count(f) AS boosted
            """
        )
        record = await result.single()
        boosted = record["boosted"] if record else 0

    log.info("semantic_memory.consolidate_done", facts_boosted=boosted)
    return {"facts_boosted": boosted}
