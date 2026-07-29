from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import structlog
from neo4j import AsyncGraphDatabase, AsyncDriver

from transform_service import person_resolver
from transform_service.models import ExtractedMeeting
from transform_service.utils import uuid5_id, with_retry

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
        # Vector indexes for semantic search — 768 dims matches LM Studio's
        # text-embedding-nomic-embed-text-v1.5. CREATE VECTOR INDEX is naturally
        # idempotent (no error on re-run), unlike constraints.
        'CREATE VECTOR INDEX meeting_embedding_idx ON :Meeting(embedding) '
        'WITH CONFIG {"dimension": 768, "capacity": 2048, "metric": "cos"}',
        'CREATE VECTOR INDEX fact_embedding_idx ON :Fact(embedding) '
        'WITH CONFIG {"dimension": 768, "capacity": 2048, "metric": "cos"}',
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


async def get_known_people() -> List[Dict[str, Any]]:
    """Return existing Person nodes for P3 probabilistic resolution (email, name, tracked)."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (p:Person) WHERE p.email IS NOT NULL
            RETURN p.email AS email, p.name AS name, coalesce(p.tracked, false) AS tracked
            """
        )
        return [dict(r) async for r in result]


@with_retry(max_attempts=3, base_delay=1.0)
async def upsert_meeting_graph(meeting: ExtractedMeeting, source_id: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    meeting_id = uuid5_id("meeting", source_id)

    # P3: resolve attendees to canonical people BEFORE writing (deterministic email
    # normalization + roster, then fuzzy name match; unresolved held for review, never
    # silently dropped). Reads happen outside the write transaction.
    roster = person_resolver.load_roster()
    known_people = await get_known_people()
    resolved, reviews = person_resolver.resolve_attendees(meeting.attendees, roster, known_people)

    driver = get_driver()
    async with driver.session() as session:
        async with await session.begin_transaction() as tx:
            # Meeting node
            await tx.run(
                """
                MERGE (m:Meeting {id: $id})
                ON CREATE SET m.created_at = $now, m.relevance_weight = 1.0
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

            # Person + Organization + ATTENDED + WORKS_AT (resolved to canonical people).
            for res in resolved:
                email = res.email  # canonical, normalized
                person_id = uuid5_id("person", email)
                domain = email.split("@")[-1] if "@" in email else "unknown"
                org_id = uuid5_id("org", domain)

                await tx.run(
                    """
                    MERGE (p:Person {email: $email})
                    ON CREATE SET p.created_at = $now, p.tracked = $tracked
                    SET p.name = $name, p.id = $person_id, p.updated_at = $now,
                        p.tracked = CASE WHEN $tracked THEN true ELSE coalesce(p.tracked, false) END

                    MERGE (o:Organization {domain: $domain})
                    ON CREATE SET o.created_at = $now
                    SET o.id = $org_id, o.updated_at = $now

                    WITH p, o
                    MERGE (p)-[:WORKS_AT]->(o)

                    WITH p
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (p)-[:ATTENDED {role: $role}]->(m)
                    """,
                    email=email,
                    name=res.name,
                    person_id=person_id,
                    tracked=res.tracked,
                    domain=domain,
                    org_id=org_id,
                    role=res.role,
                    meeting_id=meeting_id,
                    now=now,
                )

            # Unresolved attendees are HELD for review (never silently dropped).
            for rev in reviews:
                review_id = uuid5_id("person-review", f"{source_id}:{rev.name}:{rev.role}")
                await tx.run(
                    """
                    MERGE (r:PersonReview {id: $id})
                    ON CREATE SET r.created_at = $now
                    SET r.name = $name, r.role = $role, r.reason = $reason,
                        r.status = 'pending', r.updated_at = $now
                    WITH r
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:NEEDS_REVIEW]->(r)
                    """,
                    id=review_id, name=rev.name, role=rev.role, reason=rev.reason,
                    meeting_id=meeting_id, now=now,
                )

            # Topic nodes + DISCUSSED edges. MERGE key is normalized (lowercase+strip) to
            # match topic_id's existing normalization — using the raw-case name as the
            # MERGE key here used to create a second Topic node (and a colliding .id, since
            # two case variants hash to the same uuid5) for every case variant, fragmenting
            # a single real topic across nodes and understating it in every insight query
            # (bridges/communities/pagerank all key off these nodes).
            for topic_name in meeting.topics:
                norm_name = topic_name.lower().strip()
                topic_id = uuid5_id("topic", norm_name)
                await tx.run(
                    """
                    MERGE (t:Topic {name: $name})
                    ON CREATE SET t.created_at = $now
                    SET t.id = $topic_id, t.updated_at = $now

                    WITH t
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:DISCUSSED]->(t)
                    """,
                    name=norm_name,
                    topic_id=topic_id,
                    meeting_id=meeting_id,
                    now=now,
                )

            # Decision nodes + PRODUCED edges
            for i, decision in enumerate(meeting.decisions):
                decision_id = uuid5_id("decision", f"{source_id}:{i}")
                await tx.run(
                    """
                    MERGE (d:Decision {id: $id})
                    ON CREATE SET d.created_at = $now
                    SET d.text = $text, d.confidence = $confidence, d.updated_at = $now

                    WITH d
                    MATCH (m:Meeting {id: $meeting_id})
                    MERGE (m)-[:PRODUCED]->(d)
                    """,
                    id=decision_id,
                    text=decision.text,
                    confidence=decision.confidence,
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
                        a.is_engineering_task = $is_engineering_task,
                        a.confidence = $confidence,
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
                    is_engineering_task=action.is_engineering_task,
                    confidence=action.confidence,
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


async def get_open_actions_for_owner(owner: str, exclude_id: str) -> List[Dict[str, Any]]:
    """P5: open (not done) ActionItems for an owner — dedup candidates for a new item."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting)-[:FOLLOWS_UP]->(a:ActionItem)
            WHERE a.owner = $owner AND a.id <> $exclude_id
              AND coalesce(a.done, false) = false
            RETURN a.id AS id, a.jira_key AS jira_key, a.task AS task,
                   a.embedding AS embedding, m.title AS meeting_title
            """,
            owner=owner, exclude_id=exclude_id,
        )
        return [dict(r) async for r in result]


async def link_action_mentioned_in(action_id: str, meeting_id: str) -> None:
    """P5: record that an existing ActionItem was raised again in a later Meeting."""
    driver = get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (a:ActionItem {id: $action_id})
            MATCH (m:Meeting {id: $meeting_id})
            MERGE (a)-[r:MENTIONED_IN]->(m)
            ON CREATE SET r.created_at = $now
            """,
            action_id=action_id, meeting_id=meeting_id,
            now=datetime.now(timezone.utc).isoformat(),
        )


async def mark_action_needs_review(action_id: str, confidence: float) -> None:
    """P4: gate a low-confidence ActionItem into review instead of a Jira issue."""
    driver = get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (a:ActionItem {id: $id})
            SET a.jira_status = 'needs_review', a.review_confidence = $confidence, a.updated_at = $now
            """,
            id=action_id, confidence=confidence, now=datetime.now(timezone.utc).isoformat(),
        )


async def get_action_confidence(jira_key: str) -> Optional[float]:
    """Return the confidence of the ActionItem carrying this jira_key, or None if none.

    Used as the dev-agent's independent, pre-pickup gate (P4) — a low-confidence item is
    not auto-implemented even if it is labelled for the agent.
    """
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (a:ActionItem {jira_key: $key}) RETURN a.confidence AS c LIMIT 1",
            key=jira_key,
        )
        rec = await result.single()
        return float(rec["c"]) if rec and rec["c"] is not None else None


async def update_action_jira_status(jira_key: str, status: str) -> bool:
    """Update the matching ActionItem's Jira status. Returns True iff a node was matched.

    The read-back loop (jira_agent) needs to know whether the key existed so its
    matched/unmatched counters mean something — a silent no-op used to be reported as
    a match. We measure it from the write counters rather than guessing.
    """
    driver = get_driver()
    done = status.lower() in ("done", "closed", "resolved")
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:ActionItem {jira_key: $jira_key})
            SET a.jira_status = $status, a.done = $done, a.updated_at = $now
            """,
            jira_key=jira_key,
            status=status,
            done=done,
            now=datetime.now(timezone.utc).isoformat(),
        )
        summary = await result.consume()
        return summary.counters.properties_set > 0


@with_retry(max_attempts=3, base_delay=1.0)
async def merge_blocker(
    description: str,
    ticket_key: Optional[str] = None,
    raised_by: str = "dev-agent",
) -> str:
    """MERGE a lightweight Blocker node inline (P9), optionally raised by a Ticket.

    No dedicated extraction pipeline — a Blocker is created wherever it is first
    referenced. Deterministic id from the normalized text so the same blocker dedupes.
    Edge vocabulary mirrors Matteo's ontology (a Ticket/DevLog `raises_blocker`).
    Returns the blocker id, or "" for empty text.
    """
    if not description or not description.strip():
        return ""
    now = datetime.now(timezone.utc).isoformat()
    blocker_id = uuid5_id("blocker", description.strip().lower()[:200])
    driver = get_driver()
    async with driver.session() as session:
        async with await session.begin_transaction() as tx:
            await tx.run(
                """
                MERGE (b:Blocker {id: $id})
                ON CREATE SET b.created_at = $now
                SET b.description = $desc, b.raised_by = $raised_by, b.updated_at = $now
                """,
                id=blocker_id, desc=description[:500], raised_by=raised_by, now=now,
            )
            if ticket_key:
                await tx.run(
                    """
                    MATCH (b:Blocker {id: $id})
                    OPTIONAL MATCH (t:Ticket {key: $key})
                    FOREACH (_ IN CASE WHEN t IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (t)-[:RAISES_BLOCKER]->(b)
                    )
                    """,
                    id=blocker_id, key=ticket_key,
                )
            await tx.commit()
    log.info("memgraph.blocker_merged", blocker_id=blocker_id, ticket_key=ticket_key)
    return blocker_id


@with_retry(max_attempts=3, base_delay=1.0)
async def merge_ticket_resolved_by_pr(
    ticket_key: str,
    pr_url: str,
    ticket_summary: str = "",
    ticket_status: str = "Done",
    ticket_jira_url: str = "",
    merged_at: Optional[str] = None,
) -> Dict[str, str]:
    """Close the dev-agent loop: MERGE (:Ticket)-[:RESOLVED_BY]->(:PullRequest), ACID.

    Node ID derivation is the single source of truth in dev_agent/lifecycle.py:
      ticket id = uuid5("ticket", key); PR id = uuid5("pullrequest", url).
    We recompute the same uuid5(namespace, value) here so writer and reader never drift.
    Any ActionItem already carrying this ticket's jira_key is marked done in the same
    transaction, so the graph reflects real completion once the PR merges.
    """
    now = datetime.now(timezone.utc).isoformat()
    merged_at = merged_at or now
    ticket_id = uuid5_id("ticket", ticket_key)
    pr_id = uuid5_id("pullrequest", pr_url)

    driver = get_driver()
    async with driver.session() as session:
        async with await session.begin_transaction() as tx:
            await tx.run(
                """
                MERGE (t:Ticket {id: $ticket_id})
                ON CREATE SET t.created_at = $now
                SET t.key = $key, t.summary = $summary, t.status = $status,
                    t.url = $jira_url, t.updated_at = $now
                MERGE (pr:PullRequest {id: $pr_id})
                ON CREATE SET pr.created_at = $now
                SET pr.url = $pr_url, pr.merged_at = $merged_at, pr.updated_at = $now
                MERGE (t)-[r:RESOLVED_BY]->(pr)
                ON CREATE SET r.created_at = $now
                """,
                ticket_id=ticket_id, pr_id=pr_id, key=ticket_key, summary=ticket_summary,
                status=ticket_status, jira_url=ticket_jira_url, pr_url=pr_url,
                merged_at=merged_at, now=now,
            )
            # Mark any linked ActionItem completed (measured, not guessed).
            await tx.run(
                """
                MATCH (a:ActionItem {jira_key: $key})
                SET a.jira_status = 'Done', a.done = true, a.updated_at = $now
                """,
                key=ticket_key, now=now,
            )
            await tx.commit()
    log.info("memgraph.resolved_by_merged", ticket_key=ticket_key, pr_url=pr_url,
             ticket_id=ticket_id, pr_id=pr_id)
    return {"ticket_id": ticket_id, "pr_id": pr_id}


@with_retry(max_attempts=3, base_delay=1.0)
async def write_run_provenance(
    ticket_key: str,
    attempt: int,
    pr_url: str,
    pr_number: Optional[int] = None,
    branch: str = "",
    ticket_summary: str = "",
    status: str = "pr_opened",
    verified: Optional[bool] = None,
) -> Dict[str, str]:
    """Record a dev-agent run's provenance in one ACID transaction (P2).

    Writes the ``AgentRun`` bridge node (the DevLog-equivalent from Matteo's ontology)
    and connects it, using his predicate vocabulary, so one traversal returns
    meeting -> action item -> ticket -> agent run -> PR:

      (AgentRun)-[:IMPLEMENTS]->(Ticket)          # his DevLog `implements` Feature
      (AgentRun)-[:PRODUCED]->(PullRequest)       # our plan
      (AgentRun)-[:FOLLOWS_UP_ON]->(Meeting)      # his DevLog `follows_up_on` Meeting
      (ActionItem)-[:TICKETED_AS]->(Ticket)       # stitches meeting-side to dev-agent-side

    Node ids are re-derived to match dev_agent/lifecycle.py exactly (writer/reader parity
    is a known past bug class): run=uuid5("dev-agent-run", f"{key}#{attempt}"),
    ticket=uuid5("ticket", key), pr=uuid5("pullrequest", url). ``verified`` carries the
    P8 self-verification verdict (None = not checked yet).
    """
    now = datetime.now(timezone.utc).isoformat()
    run_id = uuid5_id("dev-agent-run", f"{ticket_key}#{attempt}")
    ticket_id = uuid5_id("ticket", ticket_key)
    pr_id = uuid5_id("pullrequest", pr_url)

    driver = get_driver()
    async with driver.session() as session:
        async with await session.begin_transaction() as tx:
            await tx.run(
                """
                MERGE (t:Ticket {id: $ticket_id})
                ON CREATE SET t.created_at = $now
                SET t.key = $key, t.summary = $summary, t.updated_at = $now

                MERGE (pr:PullRequest {id: $pr_id})
                ON CREATE SET pr.created_at = $now
                SET pr.url = $pr_url, pr.number = $pr_number, pr.branch = $branch,
                    pr.updated_at = $now

                MERGE (run:AgentRun {id: $run_id})
                ON CREATE SET run.created_at = $now
                SET run.ticket_key = $key, run.attempt = $attempt, run.status = $status,
                    run.branch = $branch, run.verified = $verified, run.updated_at = $now

                MERGE (run)-[:IMPLEMENTS]->(t)
                MERGE (run)-[:PRODUCED]->(pr)
                """,
                ticket_id=ticket_id, pr_id=pr_id, run_id=run_id, key=ticket_key,
                summary=ticket_summary, pr_url=pr_url, pr_number=pr_number, branch=branch,
                attempt=attempt, status=status, verified=verified, now=now,
            )
            # Stitch the meeting-side graph to the dev-agent-side graph, and mirror
            # his DevLog->Meeting `follows_up_on` shortcut. Both are OPTIONAL: a ticket
            # may exist with no extracted ActionItem (e.g. a human-created ticket).
            await tx.run(
                """
                MATCH (t:Ticket {id: $ticket_id})
                MATCH (run:AgentRun {id: $run_id})
                OPTIONAL MATCH (a:ActionItem {jira_key: $key})
                FOREACH (_ IN CASE WHEN a IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (a)-[:TICKETED_AS]->(t)
                )
                WITH run, a
                OPTIONAL MATCH (m:Meeting)-[:FOLLOWS_UP]->(a)
                FOREACH (_ IN CASE WHEN m IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (run)-[:FOLLOWS_UP_ON]->(m)
                )
                """,
                ticket_id=ticket_id, run_id=run_id, key=ticket_key,
            )
            await tx.commit()

    log.info(
        "memgraph.run_provenance_written",
        ticket_key=ticket_key, attempt=attempt, pr_url=pr_url,
        run_id=run_id, ticket_id=ticket_id, pr_id=pr_id, verified=verified,
    )
    return {"run_id": run_id, "ticket_id": ticket_id, "pr_id": pr_id}


@with_retry(max_attempts=3, base_delay=1.0)
async def write_commits_and_files(branch: str, commits: List[Dict[str, Any]]) -> Dict[str, int]:
    """Attach commits + file changes from a GitHub push to the branch's PullRequest node (P2).

    One ACID transaction. FileChange ids are (sha, path)-scoped so the same path across two
    commits stays two distinct nodes. Commits whose branch has no PullRequest node yet are a
    no-op (the MATCH yields no rows) — provenance must run first (it creates the PR node).
    """
    if not commits:
        return {"commits": 0, "files": 0}
    now = datetime.now(timezone.utc).isoformat()
    n_files = 0
    driver = get_driver()
    async with driver.session() as session:
        async with await session.begin_transaction() as tx:
            for c in commits:
                sha = c.get("sha", "")
                if not sha:
                    continue
                files = [
                    {
                        "id": uuid5_id("filechange", f"{sha}:{f['path']}"),
                        "path": f["path"],
                        "change_type": f.get("change_type", "modified"),
                    }
                    for f in c.get("files", [])
                ]
                n_files += len(files)
                await tx.run(
                    """
                    MATCH (pr:PullRequest {branch: $branch})
                    MERGE (commit:Commit {sha: $sha})
                    ON CREATE SET commit.created_at = $now
                    SET commit.message = $message, commit.updated_at = $now
                    MERGE (pr)-[:CONTAINS]->(commit)
                    WITH commit
                    UNWIND $files AS f
                        MERGE (fc:FileChange {id: f.id})
                        ON CREATE SET fc.created_at = $now
                        SET fc.path = f.path, fc.change_type = f.change_type, fc.updated_at = $now
                        MERGE (commit)-[:MODIFIES]->(fc)
                    """,
                    branch=branch, sha=sha, message=(c.get("message", "") or "")[:1000],
                    files=files, now=now,
                )
            await tx.commit()
    log.info("memgraph.commits_written", branch=branch, commits=len(commits), files=n_files)
    return {"commits": len(commits), "files": n_files}


async def migrate_schema_v5(extractor_version: str = "v5") -> Dict[str, int]:
    """Phase 32 additive migration — idempotent (MERGE-only), safe to re-run.

    1. Ensures constraints for the new node types (Ticket/PullRequest/Team/Project).
    2. Backfills provenance defaults (extracted_at/extractor_version) on extracted nodes
       that lack them — never overwrites existing provenance.
    3. Adds (:Meeting)-[:MENTIONS]->(:Ticket) for ticket keys found in meeting text
       (regex via utils.extract_ticket_keys; no LLM, no re-extraction).

    Returns per-change counts. Running twice yields identical graph (counts of *new*
    changes drop to zero on the second run).
    """
    from transform_service.utils import extract_ticket_keys

    now = datetime.now(timezone.utc).isoformat()
    driver = get_driver()
    counts = {"constraints": 0, "provenance_backfilled": 0, "mentions_added": 0}

    new_constraints = [
        "CREATE CONSTRAINT ON (t:Ticket) ASSERT t.id IS UNIQUE",
        "CREATE CONSTRAINT ON (pr:PullRequest) ASSERT pr.id IS UNIQUE",
        "CREATE CONSTRAINT ON (run:AgentRun) ASSERT run.id IS UNIQUE",
        "CREATE CONSTRAINT ON (c:Commit) ASSERT c.sha IS UNIQUE",
        "CREATE CONSTRAINT ON (fc:FileChange) ASSERT fc.id IS UNIQUE",
        "CREATE CONSTRAINT ON (b:Blocker) ASSERT b.id IS UNIQUE",
        "CREATE CONSTRAINT ON (pr:PersonReview) ASSERT pr.id IS UNIQUE",
        "CREATE CONSTRAINT ON (tm:Team) ASSERT tm.name IS UNIQUE",
        "CREATE CONSTRAINT ON (pj:Project) ASSERT pj.key IS UNIQUE",
    ]
    async with driver.session() as session:
        for cypher in new_constraints:
            try:
                await session.run(cypher)
                counts["constraints"] += 1
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    log.warning("migrate_v5.constraint_warning", cypher=cypher, error=str(exc))

        # Provenance backfill on extracted node types missing extractor_version.
        res = await session.run(
            """
            MATCH (n) WHERE (n:Meeting OR n:Decision OR n:ActionItem OR n:Fact)
                        AND n.extractor_version IS NULL
            SET n.extractor_version = $ver, n.extracted_at = coalesce(n.created_at, $now)
            RETURN count(n) AS c
            """,
            ver=extractor_version, now=now,
        )
        rec = await res.single()
        counts["provenance_backfilled"] = (rec and rec["c"]) or 0

        # MENTIONS from regex over meeting title + summary.
        meetings = await session.run("MATCH (m:Meeting) RETURN m.id AS id, m.title AS title, m.summary AS summary")
        rows = [dict(r) async for r in meetings]

    for row in rows:
        keys = extract_ticket_keys(f"{row.get('title') or ''} {row.get('summary') or ''}")
        for key in keys:
            async with driver.session() as session:
                await session.run(
                    """
                    MATCH (m:Meeting {id: $mid})
                    MERGE (t:Ticket {id: $tid})
                    ON CREATE SET t.created_at = $now, t.key = $key
                    MERGE (m)-[r:MENTIONS]->(t)
                    ON CREATE SET r.created_at = $now, r.source = 'regex'
                    """,
                    mid=row["id"], tid=uuid5_id("ticket", key), key=key, now=now,
                )
            counts["mentions_added"] += 1
    log.info("migrate_v5.done", **counts)
    return counts


async def get_meetings_quality_inputs() -> List[Dict[str, Any]]:
    """Per-meeting raw features for quality scoring (Phase 31). Counts are graph-derived.

    Decisions link via (Meeting)-[:PRODUCED]->(Decision); action items via
    (Meeting)-[:FOLLOWS_UP]->(ActionItem). ``agenda_text`` falls back to the summary since
    calendar descriptions are not stored on the node yet (documented follow-up).
    """
    driver = get_driver()
    rows: List[Dict[str, Any]] = []
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting)
            OPTIONAL MATCH (m)-[:PRODUCED]->(d:Decision)
            OPTIONAL MATCH (m)-[:FOLLOWS_UP]->(a:ActionItem)
            OPTIONAL MATCH (m)<-[:ATTENDED]-(p:Person)
            WITH m,
                 count(DISTINCT d) AS n_decisions,
                 count(DISTINCT a) AS n_actions,
                 count(DISTINCT CASE WHEN a.done THEN a END) AS n_actions_done,
                 count(DISTINCT p) AS n_attended
            RETURN m.id AS id, m.title AS title, m.duration_minutes AS duration_minutes,
                   m.summary AS summary, n_decisions, n_actions, n_actions_done, n_attended
            """
        )
        async for rec in result:
            rows.append({
                "id": rec["id"],
                "title": rec["title"],
                "duration_minutes": rec["duration_minutes"],
                "agenda_text": rec["summary"],
                "n_decisions": rec["n_decisions"] or 0,
                "n_actions": rec["n_actions"] or 0,
                "n_actions_done": rec["n_actions_done"] or 0,
                "attended": rec["n_attended"] or 0,
                "invited": None,  # calendar invited-count wiring is a follow-up
                "recurrence_scores": [],
            })
    return rows


async def get_meetings_quality_ranked(limit: int = 20) -> List[Dict[str, Any]]:
    """Meetings that have a quality_score, ranked worst-first (for the demo endpoint)."""
    driver = get_driver()
    rows: List[Dict[str, Any]] = []
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting) WHERE m.quality_score IS NOT NULL
            RETURN m.title AS title, m.quality_score AS quality_score,
                   m.quality_components AS components, m.quality_computed_at AS computed_at
            ORDER BY m.quality_score ASC
            LIMIT $limit
            """,
            limit=limit,
        )
        async for rec in result:
            rows.append({
                "title": rec["title"],
                "quality_score": rec["quality_score"],
                "components": rec["components"],
                "computed_at": rec["computed_at"],
            })
    return rows


async def set_meeting_quality(
    meeting_id: str, quality_score: Optional[float], components: Dict[str, Any]
) -> None:
    """Persist Phase 31 quality onto the Meeting node (MERGE, single statement)."""
    now = datetime.now(timezone.utc).isoformat()
    driver = get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MERGE (m:Meeting {id: $id})
            SET m.quality_score = $score,
                m.quality_components = $components,
                m.quality_computed_at = $now
            """,
            id=meeting_id,
            score=quality_score,
            components={k: v for k, v in components.items() if v is not None},
            now=now,
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
            name=name.strip().lower(),  # match the write-side normalization
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


def _group_meeting_provenance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """B4: fold flat Cypher rows from get_meeting_provenance into the nested
    meeting -> decisions / action_items[ticket, agent_runs[pull_request[commits[files]]]]
    shape. Pure — no I/O — so it is unit-testable without a driver."""
    if not rows:
        return {"meeting": None, "decisions": [], "action_items": []}

    first = rows[0]
    meeting = {"id": first["meeting_id"], "title": first["meeting_title"], "date": first.get("meeting_date")}
    decisions = [dict(d) for d in (first.get("decisions") or []) if d is not None]

    actions: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        aid = row.get("action_id")
        if aid is None:
            continue
        action = actions.setdefault(aid, {
            "id": aid, "task": row.get("action_task"), "confidence": row.get("action_confidence"),
            "owner": row.get("action_owner"),
            "ticket": {"key": row["ticket_key"], "summary": row.get("ticket_summary")} if row.get("ticket_key") else None,
            "agent_runs": [],
        })
        _fold_run(action["agent_runs"], row)

    return {"meeting": meeting, "decisions": decisions, "action_items": list(actions.values())}


def _group_ticket_provenance(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """B4: fold flat Cypher rows from get_ticket_provenance (reverse direction, anchored
    at a Ticket) into ticket -> meetings[] / agent_runs[pull_request[commits[files]]]."""
    if not rows:
        return None

    first = rows[0]
    ticket = {"key": first["ticket_key"], "summary": first.get("ticket_summary")}
    meetings: Dict[str, Dict[str, Any]] = {}
    agent_runs: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("meeting_id"):
            meetings.setdefault(row["meeting_id"], {"id": row["meeting_id"], "title": row.get("meeting_title")})
        _fold_run(agent_runs, row)

    return {"ticket": ticket, "meetings": list(meetings.values()), "agent_runs": agent_runs}


def _fold_run(agent_runs: List[Dict[str, Any]], row: Dict[str, Any]) -> None:
    """Shared row-folding for both traversal directions: dedupe/merge one row's
    run -> pull_request -> commit -> file chain into an existing agent_runs list."""
    rid = row.get("run_id")
    if rid is None:
        return
    run = next((r for r in agent_runs if r["id"] == rid), None)
    if run is None:
        run = {
            "id": rid, "attempt": row.get("run_attempt"), "status": row.get("run_status"),
            "verified": row.get("run_verified"), "pull_request": None,
        }
        agent_runs.append(run)

    if row.get("pr_url"):
        if run["pull_request"] is None:
            run["pull_request"] = {"url": row["pr_url"], "number": row.get("pr_number"), "commits": []}
        commits = run["pull_request"]["commits"]
        sha = row.get("commit_sha")
        if sha:
            commit = next((c for c in commits if c["sha"] == sha), None)
            if commit is None:
                commit = {"sha": sha, "message": row.get("commit_message"), "files": []}
                commits.append(commit)
            path = row.get("file_path")
            if path and not any(f["path"] == path for f in commit["files"]):
                commit["files"].append({"path": path, "change_type": row.get("file_change_type")})


async def get_meeting_provenance(meeting_id: str) -> Dict[str, Any]:
    """B4: one-traversal provenance — meeting -> decision -> action item -> ticket ->
    agent run -> PR -> files, in a single Cypher MATCH. Row-grouping is pure Python
    (_group_meeting_provenance) so cross-joining independent branches (decisions vs. the
    action-item chain) stays cheap: decisions are collected to one list before the
    row-multiplying OPTIONAL MATCH chain, so they're identical (and deduped) on every row.
    """
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting {id: $meeting_id})
            OPTIONAL MATCH (m)-[:PRODUCED]->(d:Decision)
            WITH m, collect(DISTINCT {id: d.id, text: d.text, confidence: d.confidence}) AS decisions
            OPTIONAL MATCH (m)-[:FOLLOWS_UP]->(a:ActionItem)
            OPTIONAL MATCH (a)-[:TICKETED_AS]->(t:Ticket)
            OPTIONAL MATCH (run:AgentRun)-[:IMPLEMENTS]->(t)
            OPTIONAL MATCH (run)-[:PRODUCED]->(pr:PullRequest)
            OPTIONAL MATCH (pr)-[:CONTAINS]->(c:Commit)
            OPTIONAL MATCH (c)-[:MODIFIES]->(fc:FileChange)
            RETURN m.id AS meeting_id, m.title AS meeting_title, m.date AS meeting_date,
                   decisions,
                   a.id AS action_id, a.task AS action_task, a.confidence AS action_confidence,
                   a.owner AS action_owner,
                   t.key AS ticket_key, t.summary AS ticket_summary,
                   run.id AS run_id, run.attempt AS run_attempt, run.status AS run_status,
                   run.verified AS run_verified,
                   pr.url AS pr_url, pr.number AS pr_number,
                   c.sha AS commit_sha, c.message AS commit_message,
                   fc.path AS file_path, fc.change_type AS file_change_type
            """,
            meeting_id=meeting_id,
        )
        rows = [dict(r) async for r in result]
    grouped = _group_meeting_provenance(rows)
    # collect() on an unmatched Decision still yields one all-null map — drop it here so
    # a meeting with zero decisions reports [] instead of [{"id": None, ...}].
    grouped["decisions"] = [d for d in grouped["decisions"] if d.get("id") is not None]
    return grouped


async def get_ticket_provenance(ticket_key: str) -> Optional[Dict[str, Any]]:
    """B4: the reverse traversal, anchored at a Ticket — ticket -> meetings that raised it
    -> agent runs that implemented it -> PR -> files. One Cypher MATCH."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (t:Ticket {key: $ticket_key})
            OPTIONAL MATCH (a:ActionItem)-[:TICKETED_AS]->(t)
            OPTIONAL MATCH (m:Meeting)-[:FOLLOWS_UP]->(a)
            OPTIONAL MATCH (run:AgentRun)-[:IMPLEMENTS]->(t)
            OPTIONAL MATCH (run)-[:PRODUCED]->(pr:PullRequest)
            OPTIONAL MATCH (pr)-[:CONTAINS]->(c:Commit)
            OPTIONAL MATCH (c)-[:MODIFIES]->(fc:FileChange)
            RETURN t.key AS ticket_key, t.summary AS ticket_summary,
                   m.id AS meeting_id, m.title AS meeting_title,
                   a.id AS action_id, a.task AS action_task,
                   run.id AS run_id, run.attempt AS run_attempt, run.status AS run_status,
                   run.verified AS run_verified,
                   pr.url AS pr_url, pr.number AS pr_number,
                   c.sha AS commit_sha, c.message AS commit_message,
                   fc.path AS file_path, fc.change_type AS file_change_type
            """,
            ticket_key=ticket_key,
        )
        rows = [dict(r) async for r in result]
    return _group_ticket_provenance(rows)


async def get_actions_needing_review() -> List[Dict[str, Any]]:
    """B3: ActionItems P4 gated below JIRA_CONFIDENCE_THRESHOLD — written by
    mark_action_needs_review but never surfaced anywhere until now."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a:ActionItem {jira_status: 'needs_review'})
            RETURN a.id AS id, a.task AS task, a.owner AS owner,
                   a.confidence AS confidence, a.review_confidence AS review_confidence,
                   a.priority AS priority, a.jira_status AS jira_status
            ORDER BY a.confidence ASC
            """
        )
        return [dict(r) async for r in result]


async def get_person_reviews() -> List[Dict[str, Any]]:
    """B3: PersonReview nodes P3 writes for unresolved attendees — held, never dropped,
    but nothing read them back until now."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (m:Meeting)-[:NEEDS_REVIEW]->(r:PersonReview {status: 'pending'})
            RETURN r.id AS id, r.name AS name, r.role AS role, r.reason AS reason,
                   m.title AS meeting_title, m.id AS meeting_id
            ORDER BY r.created_at DESC
            """
        )
        return [dict(r) async for r in result]


async def get_open_blockers() -> List[Dict[str, Any]]:
    """B3: Blocker nodes P9 writes on a dev-agent run failure — surfaced here for the
    review queue / dashboard."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (b:Blocker)
            OPTIONAL MATCH (t:Ticket)-[:RAISES_BLOCKER]->(b)
            RETURN b.id AS id, b.description AS description, b.raised_by AS raised_by,
                   t.key AS ticket_key, b.created_at AS created_at
            ORDER BY b.created_at DESC
            """
        )
        return [dict(r) async for r in result]


async def get_influential_nodes(label: str = "Person", limit: int = 10) -> List[Dict[str, Any]]:
    """Return top N nodes by pagerank_score for a given label.

    Governance (P3): per-person rankings are gated behind the ``Person.tracked`` opt-in —
    an untracked individual is never surfaced in a leaderboard by default. Other labels
    (Topic, Meeting, ...) are unaffected.
    """
    driver = get_driver()
    tracked_gate = "AND coalesce(n.tracked, false) = true" if label == "Person" else ""
    async with driver.session() as session:
        result = await session.run(
            f"""
            MATCH (n:{label})
            WHERE n.pagerank_score IS NOT NULL {tracked_gate}
            RETURN n.id AS id,
                   COALESCE(n.name, n.title, n.task, n.text, n.summary, n.email) AS name,
                   n.pagerank_score AS pagerank_score,
                   n.community_id AS community_id
            ORDER BY n.pagerank_score DESC
            LIMIT $limit
            """,
            limit=limit,
        )
        return [dict(r) async for r in result]


async def get_community_members(community_id: int) -> List[Dict[str, Any]]:
    """Return all nodes in a given community_id."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n)
            WHERE n.community_id = $community_id
            RETURN n.id AS id,
                   COALESCE(n.name, n.title, n.task, n.text, n.summary, n.email) AS name,
                   labels(n) AS labels,
                   n.pagerank_score AS pagerank_score
            """,
            community_id=community_id,
        )
        return [dict(r) async for r in result]


async def get_bridge_nodes(limit: int = 10) -> List[Dict[str, Any]]:
    """Return top N nodes by betweenness_centrality."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n)
            WHERE n.betweenness_centrality IS NOT NULL
            RETURN n.id AS id,
                   COALESCE(n.name, n.title, n.task, n.text, n.summary, n.email) AS name,
                   labels(n) AS labels,
                   n.betweenness_centrality AS betweenness_centrality,
                   n.community_id AS community_id
            ORDER BY n.betweenness_centrality DESC
            LIMIT $limit
            """,
            limit=limit,
        )
        return [dict(r) async for r in result]


async def get_node_insights(node_id: str) -> Dict[str, Any]:
    """Return algorithm scores for a specific node."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n {id: $id})
            RETURN n.id AS id,
                   COALESCE(n.name, n.title, n.task, n.text, n.summary, n.email) AS name,
                   labels(n) AS labels,
                   n.pagerank_score AS pagerank_score,
                   n.community_id AS community_id,
                   n.betweenness_centrality AS betweenness_centrality,
                   n.degree_centrality AS degree_centrality,
                   n.wcc_id AS wcc_id
            """,
            id=node_id,
        )
        records = [dict(r) async for r in result]
        return records[0] if records else {}


async def get_all_communities() -> List[Dict[str, Any]]:
    """Return all community_ids with their member node IDs."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (n)
            WHERE n.community_id IS NOT NULL
            RETURN n.community_id AS community_id, collect(n.id) AS members
            ORDER BY community_id
            """
        )
        return [dict(r) async for r in result]
