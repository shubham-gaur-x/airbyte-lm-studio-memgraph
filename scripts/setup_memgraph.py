"""Run once before first use to create Memgraph constraints and indexes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

from transform_service import memgraph_client
from transform_service.utils import uuid5_id


_PHASE21_SCHEMA = [
    # New node uniqueness constraints
    "CREATE CONSTRAINT ON (f:Fact) ASSERT f.id IS UNIQUE",
    "CREATE CONSTRAINT ON (pref:Preference) ASSERT pref.id IS UNIQUE",
    "CREATE CONSTRAINT ON (proc:Procedure) ASSERT proc.id IS UNIQUE",
    "CREATE CONSTRAINT ON (ps:ProcedureStep) ASSERT ps.id IS UNIQUE",
    "CREATE CONSTRAINT ON (ms:MemorySession) ASSERT ms.id IS UNIQUE",
    # New indexes for algorithm scores and memory queries
    "CREATE INDEX ON :Person(community_id)",
    "CREATE INDEX ON :Person(pagerank_score)",
    "CREATE INDEX ON :Topic(community_id)",
    "CREATE INDEX ON :Meeting(relevance_weight)",
    "CREATE INDEX ON :Fact(confidence)",
    "CREATE INDEX ON :MemorySession(created_at)",
]


async def apply_phase21_schema() -> None:
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        for cypher in _PHASE21_SCHEMA:
            try:
                await session.run(cypher)
                print(f"  OK: {cypher[:60]}")
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    print(f"  WARN: {cypher[:60]} → {exc}")


_SEEDED_PROCEDURES = [
    {
        "name": "sprint_planning",
        "description": "Regular sprint planning session to groom backlog and commit to sprint goal.",
        "match_pattern": {"kind": ["meeting"], "min_attendees": 3, "topic_keywords": ["sprint", "backlog", "velocity", "story points", "standup"]},
        "steps": [
            "Review previous sprint outcomes",
            "Groom and prioritise backlog",
            "Estimate story points",
            "Commit to sprint goal",
            "Assign tasks to team members",
        ],
    },
    {
        "name": "client_review",
        "description": "Present deliverables to client and collect feedback.",
        "match_pattern": {"topic_keywords": ["client", "demo", "feedback", "presentation", "review"], "requires_multi_org": True},
        "steps": [
            "Present deliverables",
            "Collect client feedback",
            "Document decisions and change requests",
            "Agree on next steps",
        ],
    },
    {
        "name": "one_on_one",
        "description": "One-on-one meeting between two people.",
        "match_pattern": {"max_attendees": 2, "min_attendees": 2},
        "steps": [
            "Check-in on priorities",
            "Surface blockers",
            "Career development discussion",
            "Agree on action items",
        ],
    },
    {
        "name": "incident_response",
        "description": "Coordinated response to a production incident.",
        "match_pattern": {"topic_keywords": ["incident", "outage", "bug", "hotfix", "urgent", "down", "p0", "p1"]},
        "steps": [
            "Confirm incident and severity",
            "Assemble response team",
            "Diagnose root cause",
            "Apply fix or mitigation",
            "Write post-mortem",
        ],
    },
    {
        "name": "project_kickoff",
        "description": "Kickoff meeting for a new project or initiative.",
        "match_pattern": {"topic_keywords": ["kickoff", "onboarding", "new project", "launch", "initiation"]},
        "steps": [
            "Introduce stakeholders",
            "Define project scope and goals",
            "Agree on timeline and milestones",
            "Assign ownership",
            "Schedule follow-up checkpoints",
        ],
    },
    {
        "name": "retrospective",
        "description": "Sprint or project retrospective to capture lessons learned.",
        "match_pattern": {"topic_keywords": ["retro", "retrospective", "what went well", "improvements", "lessons"]},
        "steps": [
            "What went well",
            "What could be improved",
            "Action items for next iteration",
        ],
    },
]


async def seed_procedures() -> None:
    """Idempotent: MERGE Procedure and ProcedureStep nodes with their edges."""
    driver = memgraph_client.get_driver()
    now = datetime.now(timezone.utc).isoformat()

    async with driver.session() as session:
        for proc in _SEEDED_PROCEDURES:
            proc_id = uuid5_id("procedure", proc["name"])
            match_pattern_json = json.dumps(proc["match_pattern"])

            await session.run(
                """
                MERGE (p:Procedure {id: $id})
                ON CREATE SET p.name = $name,
                              p.description = $description,
                              p.match_pattern = $match_pattern,
                              p.is_inferred = false,
                              p.occurrence_count = 0,
                              p.created_at = $now
                ON MATCH SET  p.description = $description,
                              p.match_pattern = $match_pattern,
                              p.updated_at = $now
                """,
                id=proc_id,
                name=proc["name"],
                description=proc["description"],
                match_pattern=match_pattern_json,
                now=now,
            )
            print(f"  MERGE Procedure: {proc['name']}")

            prev_step_id = None
            for order, step_name in enumerate(proc["steps"], start=1):
                step_id = uuid5_id("step", f"{proc['name']}:{order}")

                await session.run(
                    """
                    MERGE (s:ProcedureStep {id: $step_id})
                    ON CREATE SET s.name = $name,
                                  s.description = $name,
                                  s.order = $order,
                                  s.created_at = $now
                    ON MATCH SET  s.name = $name, s.order = $order
                    WITH s
                    MATCH (p:Procedure {id: $proc_id})
                    MERGE (p)-[:HAS_STEP]->(s)
                    """,
                    step_id=step_id,
                    name=step_name,
                    order=order,
                    proc_id=proc_id,
                    now=now,
                )

                if prev_step_id:
                    await session.run(
                        """
                        MATCH (prev:ProcedureStep {id: $prev_id})
                        MATCH (curr:ProcedureStep {id: $curr_id})
                        MERGE (prev)-[:NEXT_STEP]->(curr)
                        """,
                        prev_id=prev_step_id,
                        curr_id=step_id,
                    )

                prev_step_id = step_id

    print("Procedures seeded.")


async def main() -> None:
    print("Setting up Memgraph constraints and indexes...")
    await memgraph_client.create_indexes()
    print("Applying Phase 21 schema (memory + algorithm indexes)...")
    await apply_phase21_schema()
    print("Seeding procedures (Phase 24)...")
    await seed_procedures()
    print("Done. Memgraph is ready.")
    await memgraph_client.close_driver()


if __name__ == "__main__":
    asyncio.run(main())
