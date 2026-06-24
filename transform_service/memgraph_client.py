from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import structlog
from neo4j import AsyncGraphDatabase, AsyncDriver

from transform_service.models import ExtractedMeeting
from transform_service.utils import uuid5_id

log = structlog.get_logger()
_driver: Optional[AsyncDriver] = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        host = os.environ.get("MEMGRAPH_HOST", "memgraph")
        port = os.environ.get("MEMGRAPH_PORT", "7687")
        user = os.environ.get("MEMGRAPH_USER", "")
        password = os.environ.get("MEMGRAPH_PASSWORD", "")
        uri = f"bolt://{host}:{port}"
        _driver = AsyncGraphDatabase.driver(uri, auth=(user, password) if user else None)
        log.info("memgraph.driver_created", uri=uri)
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def create_indexes() -> None:
    driver = get_driver()
    constraints = [
        "CREATE CONSTRAINT ON (m:Meeting) ASSERT m.id IS UNIQUE",
        "CREATE CONSTRAINT ON (p:Person) ASSERT p.email IS UNIQUE",
        "CREATE CONSTRAINT ON (t:Topic) ASSERT t.name IS UNIQUE",
        "CREATE CONSTRAINT ON (d:Decision) ASSERT d.id IS UNIQUE",
        "CREATE CONSTRAINT ON (a:ActionItem) ASSERT a.id IS UNIQUE",
        "CREATE CONSTRAINT ON (o:Organization) ASSERT o.domain IS UNIQUE",
        "CREATE INDEX ON :Meeting(date)",
        "CREATE INDEX ON :Meeting(created_at)",
        "CREATE INDEX ON :ActionItem(created_at)",
        "CREATE INDEX ON :Decision(created_at)",
    ]
    async with driver.session() as session:
        for cypher in constraints:
            try:
                await session.run(cypher)
            except Exception as exc:
                # Memgraph raises if constraint already exists — safe to ignore
                if "already exists" not in str(exc).lower():
                    log.warning("memgraph.index_warning", cypher=cypher, error=str(exc))
    log.info("memgraph.indexes_ready")


async def upsert_meeting_graph(meeting: ExtractedMeeting, source_id: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    meeting_id = uuid5_id("meeting", source_id)

    driver = get_driver()
    async with driver.session() as session:
        async with await session.begin_transaction() as tx:
            # Meeting node
            await tx.run(
                """
                MERGE (m:Meeting {id: $id})
                ON CREATE SET m.created_at = $now
                SET m.title = $title,
                    m.kind = $kind,
                    m.platform = $platform,
                    m.date = $date,
                    m.duration_minutes = $duration,
                    m.summary = $summary,
                    m.sentiment = $sentiment,
                    m.follow_up_needed = $follow_up,
                    m.confidence = $confidence,
                    m.source_id = $source_id,
                    m.updated_at = $now
                """,
                id=meeting_id,
                title=meeting.title,
                kind=meeting.kind,
                platform=meeting.platform,
                date=str(meeting.date),
                duration=meeting.duration_minutes,
                summary=meeting.summary,
                sentiment=meeting.sentiment,
                follow_up=meeting.follow_up_needed,
                confidence=meeting.confidence,
                source_id=source_id,
                now=now,
            )

            # Person + Organization + ATTENDED + WORKS_AT
            for attendee in meeting.attendees:
                if not attendee.email:
                    continue
                person_id = uuid5_id("person", attendee.email)
                domain = attendee.email.split("@")[-1] if "@" in attendee.email else "unknown"
                org_id = uuid5_id("org", domain)

                await tx.run(
                    """
                    MERGE (p:Person {email: $email})
                    ON CREATE SET p.created_at = $now
                    SET p.name = $name, p.id = $person_id, p.updated_at = $now

                    MERGE (o:Organization {domain: $domain})
                    ON CREATE SET o.created_at = $now
                    SET o.id = $org_id, o.updated_at = $now

                    WITH p, o
                    MERGE (p)-[:WORKS_AT]->(o)

                    WITH p
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (p)-[:ATTENDED {role: $role}]->(m)
                    """,
                    email=attendee.email,
                    name=attendee.name,
                    person_id=person_id,
                    domain=domain,
                    org_id=org_id,
                    role=attendee.role,
                    meeting_id=meeting_id,
                    now=now,
                )

            # Topic nodes + DISCUSSED edges
            for topic_name in meeting.topics:
                topic_id = uuid5_id("topic", topic_name.lower().strip())
                await tx.run(
                    """
                    MERGE (t:Topic {name: $name})
                    ON CREATE SET t.created_at = $now
                    SET t.id = $topic_id, t.updated_at = $now

                    WITH t
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:DISCUSSED]->(t)
                    """,
                    name=topic_name,
                    topic_id=topic_id,
                    meeting_id=meeting_id,
                    now=now,
                )

            # Decision nodes + PRODUCED edges
            for i, decision_text in enumerate(meeting.decisions):
                decision_id = uuid5_id("decision", f"{source_id}:{i}")
                await tx.run(
                    """
                    MERGE (d:Decision {id: $id})
                    ON CREATE SET d.created_at = $now
                    SET d.text = $text, d.updated_at = $now

                    WITH d
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:PRODUCED]->(d)
                    """,
                    id=decision_id,
                    text=decision_text,
                    meeting_id=meeting_id,
                    now=now,
                )

            # ActionItem nodes + ASSIGNED_TO + FOLLOWS_UP edges
            for i, action in enumerate(meeting.action_items):
                action_id = uuid5_id("action", f"{source_id}:{i}:{action.task}")
                await tx.run(
                    """
                    MERGE (a:ActionItem {id: $id})
                    ON CREATE SET a.created_at = $now
                    SET a.task = $task,
                        a.owner = $owner,
                        a.due = $due,
                        a.done = $done,
                        a.priority = $priority,
                        a.updated_at = $now

                    WITH a
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:FOLLOWS_UP]->(a)

                    WITH a
                    OPTIONAL MATCH (p:Person {email: $owner_email})
                    FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (a)-[:ASSIGNED_TO]->(p)
                    )
                    """,
                    id=action_id,
                    task=action.task,
                    owner=action.owner,
                    due=str(action.due) if action.due else None,
                    done=action.done,
                    priority=action.priority,
                    meeting_id=meeting_id,
                    owner_email=action.owner if "@" in action.owner else None,
                    now=now,
                )

            await tx.commit()

    log.info(
        "memgraph.meeting_upserted",
        meeting_id=meeting_id,
        title=meeting.title,
        attendees=len(meeting.attendees),
        topics=len(meeting.topics),
        actions=len(meeting.action_items),
    )
    return meeting_id


async def update_action_jira_key(action_id: str, jira_key: str) -> None:
    driver = get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (a:ActionItem {id: $id})
            SET a.jira_key = $jira_key, a.jira_status = 'Open', a.updated_at = $now
            """,
            id=action_id,
            jira_key=jira_key,
            now=datetime.now(timezone.utc).isoformat(),
        )


async def update_action_jira_status(jira_key: str, status: str) -> None:
    driver = get_driver()
    done = status.lower() in ("done", "closed", "resolved")
    async with driver.session() as session:
        await session.run(
            """
            MATCH (a:ActionItem {jira_key: $jira_key})
            SET a.jira_status = $status, a.done = $done, a.updated_at = $now
            """,
            jira_key=jira_key,
            status=status,
            done=done,
            now=datetime.now(timezone.utc).isoformat(),
        )


async def get_timeline(window: Literal["day", "week", "month"]) -> Dict[str, Any]:
    from datetime import timedelta
    hours = {"day": 24, "week": 168, "month": 720}[window]
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    driver = get_driver()
    async with driver.session() as session:
        meetings_result = await session.run(
            """
            MATCH (m:Meeting)
            WHERE m.created_at >= $since
            RETURN m.id AS id, m.title AS title, m.date AS date,
                   m.kind AS kind, m.platform AS platform,
                   m.created_at AS created_at
            ORDER BY m.created_at DESC
            """,
            since=since,
        )
        meetings = [dict(r) async for r in meetings_result]

        decisions_result = await session.run(
            """
            MATCH (d:Decision)
            WHERE d.created_at >= $since
            RETURN d.id AS id, d.text AS text, d.created_at AS created_at
            ORDER BY d.created_at DESC
            """,
            since=since,
        )
        decisions = [dict(r) async for r in decisions_result]

        actions_result = await session.run(
            """
            MATCH (a:ActionItem)
            WHERE a.created_at >= $since
            RETURN a.id AS id, a.task AS task, a.owner AS owner,
                   a.due AS due, a.done AS done, a.priority AS priority,
                   a.jira_key AS jira_key, a.created_at AS created_at
            ORDER BY a.created_at DESC
            """,
            since=since,
        )
        actions = [dict(r) async for r in actions_result]

    return {"window": window, "meetings": meetings, "decisions": decisions, "action_items": actions}


async def get_recent_meetings(limit: int = 10) -> List[Dict[str, Any]]:
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting)
            RETURN m.id AS id, m.title AS title, m.date AS date,
                   m.kind AS kind, m.platform AS platform, m.summary AS summary,
                   m.sentiment AS sentiment, m.created_at AS created_at
            ORDER BY m.created_at DESC
            LIMIT $limit
            """,
            limit=limit,
        )
        return [dict(r) async for r in result]


async def get_person_graph(email: str) -> Dict[str, Any]:
    driver = get_driver()
    async with driver.session() as session:
        person_result = await session.run(
            """
            MATCH (p:Person {email: $email})
            OPTIONAL MATCH (p)-[:ATTENDED]->(m:Meeting)
            OPTIONAL MATCH (a:ActionItem)-[:ASSIGNED_TO]->(p)
            RETURN p.name AS name, p.email AS email,
                   collect(DISTINCT {id: m.id, title: m.title, date: m.date}) AS meetings,
                   collect(DISTINCT {id: a.id, task: a.task, done: a.done}) AS actions
            """,
            email=email,
        )
        records = [dict(r) async for r in person_result]
        return records[0] if records else {}


async def get_topic_graph(name: str) -> Dict[str, Any]:
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (t:Topic {name: $name})
            OPTIONAL MATCH (m:Meeting)-[:DISCUSSED]->(t)
            RETURN t.name AS name,
                   collect(DISTINCT {id: m.id, title: m.title, date: m.date}) AS meetings
            """,
            name=name,
        )
        records = [dict(r) async for r in result]
        return records[0] if records else {}


async def get_open_actions() -> List[Dict[str, Any]]:
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:ActionItem {done: false})
            RETURN a.id AS id, a.task AS task, a.owner AS owner,
                   a.due AS due, a.priority AS priority,
                   a.jira_key AS jira_key, a.jira_status AS jira_status
            ORDER BY a.priority, a.due
            """
        )
        return [dict(r) async for r in result]
