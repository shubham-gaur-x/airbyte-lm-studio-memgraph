"""
Procedural memory — known and inferred workflow procedures.
Owns: Procedure, ProcedureStep nodes; FOLLOWS_PROCEDURE, HAS_STEP, NEXT_STEP edges.
No MAGE CALL procedures here. No LM Studio calls here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from transform_service import memgraph_client
from transform_service.models import ExtractedMeeting
from transform_service.utils import uuid5_id

log = structlog.get_logger()

KNOWN_PROCEDURE_PATTERNS: dict[str, dict[str, Any]] = {
    "sprint_planning": {
        "kind": ["meeting"],
        "min_attendees": 3,
        "topic_keywords": ["sprint", "backlog", "velocity", "story points", "standup"],
    },
    "client_review": {
        "topic_keywords": ["client", "demo", "feedback", "presentation", "review"],
        "requires_multi_org": True,
    },
    "one_on_one": {
        "max_attendees": 2,
        "min_attendees": 2,
    },
    "incident_response": {
        "topic_keywords": ["incident", "outage", "bug", "hotfix", "urgent", "down", "p0", "p1"],
    },
    "project_kickoff": {
        "topic_keywords": ["kickoff", "onboarding", "new project", "launch", "initiation"],
    },
    "retrospective": {
        "topic_keywords": ["retro", "retrospective", "what went well", "improvements", "lessons"],
    },
}


def _matches_pattern(meeting: ExtractedMeeting, pattern: dict[str, Any]) -> bool:
    """Pure Python — no async, no Cypher. Check meeting against one pattern dict.
    Keyword matching is case-insensitive substring match against meeting.topics."""
    topics_lower = [t.lower() for t in meeting.topics]
    attendee_count = len(meeting.attendees)

    if "kind" in pattern:
        if meeting.kind not in pattern["kind"]:
            return False

    if "min_attendees" in pattern:
        if attendee_count < pattern["min_attendees"]:
            return False

    if "max_attendees" in pattern:
        if attendee_count > pattern["max_attendees"]:
            return False

    if "topic_keywords" in pattern:
        keywords = pattern["topic_keywords"]
        matched = any(
            any(kw in topic for topic in topics_lower)
            for kw in keywords
        )
        if not matched:
            return False

    # requires_multi_org: check if attendees come from >= 2 distinct domains
    if pattern.get("requires_multi_org"):
        domains = set()
        for a in meeting.attendees:
            if a.email and "@" in a.email:
                domains.add(a.email.split("@")[-1])
        if len(domains) < 2:
            return False

    return True


async def match_to_procedure(
    meeting: ExtractedMeeting, meeting_id: str, attendee_emails: list[str]
) -> list[str]:
    """Check meeting against all known patterns. For each match:
    - MERGE FOLLOWS_PROCEDURE edge with confidence=0.8
    - Increment Procedure.occurrence_count
    Returns list of matched procedure names."""
    now = datetime.now(timezone.utc).isoformat()
    driver = memgraph_client.get_driver()
    matched: list[str] = []

    for proc_name, pattern in KNOWN_PROCEDURE_PATTERNS.items():
        if not _matches_pattern(meeting, pattern):
            continue

        proc_id = uuid5_id("procedure", proc_name)
        try:
            async with driver.session() as session:
                await session.run(
                    """
                    MATCH (m:Meeting {id: $meeting_id})
                    MATCH (p:Procedure {id: $proc_id})
                    MERGE (m)-[:FOLLOWS_PROCEDURE {confidence: 0.8}]->(p)
                    SET p.occurrence_count = p.occurrence_count + 1,
                        p.updated_at = $now
                    """,
                    meeting_id=meeting_id,
                    proc_id=proc_id,
                    now=now,
                )
            matched.append(proc_name)
        except Exception as exc:
            log.warning(
                "procedural_memory.match_write_failed",
                proc_name=proc_name,
                meeting_id=meeting_id,
                error=str(exc),
            )

    if matched:
        log.info("procedural_memory.matched", meeting_id=meeting_id, procedures=matched)
    return matched


async def discover_procedures() -> dict:
    """Nightly job. Find groups of meetings with:
    - same community_id (persons who share a community)
    - >= 60% topic overlap between meetings in the group (set intersection)
    - >= 5 meetings not yet linked to any Procedure
    For qualifying groups: create Procedure {is_inferred: True}.
    Returns {clusters_found, procedures_created}."""
    now = datetime.now(timezone.utc).isoformat()
    driver = memgraph_client.get_driver()

    # 1. Find unmatched meetings: use OPTIONAL MATCH + IS NULL (Memgraph-compatible)
    #    Also pull community_id from attendees and topic names via DISCUSSED edges.
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting)
            OPTIONAL MATCH (m)-[:FOLLOWS_PROCEDURE]->(proc:Procedure)
            WITH m, proc WHERE proc IS NULL
            OPTIONAL MATCH (person:Person)-[:ATTENDED]->(m)
            OPTIONAL MATCH (m)-[:DISCUSSED]->(t:Topic)
            RETURN m.id AS meeting_id,
                   collect(DISTINCT person.community_id) AS community_ids,
                   collect(DISTINCT t.name) AS topic_names
            """
        )
        rows = [dict(r) async for r in result]

    # Build meeting records, picking the first non-null community_id
    meetings_by_community: dict[int, list[dict]] = {}
    for row in rows:
        cid_list = [c for c in (row.get("community_ids") or []) if c is not None]
        if not cid_list:
            continue
        cid = cid_list[0]
        topics = row.get("topic_names") or []
        meetings_by_community.setdefault(cid, []).append(
            {"id": row["meeting_id"], "topics": set(str(t).lower() for t in topics)}
        )

    clusters_found = 0
    procedures_created = 0

    for cid, ms in meetings_by_community.items():
        if len(ms) < 5:
            continue

        # 2. Cluster meetings by >= 60% Jaccard topic overlap
        # Greedy: assign each meeting to first cluster it fits; otherwise start new
        clusters: list[list[dict]] = []
        for m in ms:
            placed = False
            for cluster in clusters:
                # Compare against cluster centroid (union of all topic sets)
                centroid = set().union(*(c["topics"] for c in cluster))
                union = centroid | m["topics"]
                if not union:
                    continue
                overlap = len(centroid & m["topics"]) / len(union)
                if overlap >= 0.6:
                    cluster.append(m)
                    placed = True
                    break
            if not placed:
                clusters.append([m])

        for idx, cluster in enumerate(clusters):
            if len(cluster) < 5:
                continue

            clusters_found += 1
            proc_name = f"inferred_{cid}_{idx}"
            proc_id = uuid5_id("procedure", proc_name)
            proc_desc = f"Inferred pattern from {len(cluster)} meetings"

            try:
                async with driver.session() as session:
                    await session.run(
                        """
                        MERGE (p:Procedure {id: $proc_id})
                        ON CREATE SET p.name = $name,
                                      p.description = $description,
                                      p.is_inferred = true,
                                      p.occurrence_count = $count,
                                      p.created_at = $now
                        """,
                        proc_id=proc_id,
                        name=proc_name,
                        description=proc_desc,
                        count=len(cluster),
                        now=now,
                    )
                    for m in cluster:
                        await session.run(
                            """
                            MATCH (meeting:Meeting {id: $meeting_id})
                            MATCH (p:Procedure {id: $proc_id})
                            MERGE (meeting)-[:FOLLOWS_PROCEDURE {confidence: 0.6}]->(p)
                            """,
                            meeting_id=m["id"],
                            proc_id=proc_id,
                        )
                procedures_created += 1
                log.info(
                    "procedural_memory.procedure_inferred",
                    proc_name=proc_name,
                    size=len(cluster),
                    community_id=cid,
                )
            except Exception as exc:
                log.warning(
                    "procedural_memory.infer_write_failed",
                    proc_name=proc_name,
                    error=str(exc),
                )

    log.info(
        "procedural_memory.discover_done",
        clusters_found=clusters_found,
        procedures_created=procedures_created,
    )
    return {"clusters_found": clusters_found, "procedures_created": procedures_created}
