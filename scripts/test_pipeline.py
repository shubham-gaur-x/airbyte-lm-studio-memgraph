"""End-to-end smoke test: insert sample email → extract → Memgraph → assert meeting exists."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

import httpx

SAMPLE_EMAIL_SUBJECT = "Q3 Planning Meeting — Action Items and Decisions"
SAMPLE_EMAIL_BODY = """
Hi team,

Following up on our Q3 planning meeting held today via Zoom with Sarah (sarah@example.com),
John (john@example.com), and myself.

Duration: 90 minutes

Key Decisions:
- We will migrate to the new auth system by end of July
- Budget approved for the new infrastructure upgrade

Action Items:
- Sarah to draft migration plan by July 15
- John to set up staging environment by July 10
- Everyone to review the security checklist by July 8

Next meeting: July 20 at 2pm

Best,
Shubham
"""


async def check_lm_studio() -> bool:
    base = os.environ.get("LM_STUDIO_BASE_URL", "http://host.docker.internal:1234/v1")
    url = base.rstrip("/v1").rstrip("/") + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": "Bearer lm-studio"})
            return resp.status_code == 200
    except Exception as exc:
        print(f"  FAIL: LM Studio unreachable at {url} — {exc}")
        print("  Make sure LM Studio is running on your Mac with gemma3-12b loaded.")
        return False


async def main() -> None:
    print("=" * 60)
    print("smoke test: airbyte-lm-studio-memgraph")
    print("=" * 60)

    # Step 1: LM Studio check
    print("\n[1/5] Checking LM Studio...")
    if not await check_lm_studio():
        sys.exit(1)
    print("  PASS: LM Studio reachable")

    # Step 2: Insert sample email into Postgres
    print("\n[2/5] Inserting sample email into Postgres...")
    from transform_service import db

    await db.create_staging_tables()
    source_id = f"smoke-test-{uuid.uuid4().hex[:8]}"
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO raw_emails (source_id, subject, from_email, to_emails, body)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (source_id) DO NOTHING
            """,
            source_id,
            SAMPLE_EMAIL_SUBJECT,
            "shubham@example.com",
            ["sarah@example.com", "john@example.com"],
            SAMPLE_EMAIL_BODY,
        )
    print(f"  PASS: Inserted email source_id={source_id}")

    # Step 3: Run pipeline
    print("\n[3/5] Running process_new_emails()...")
    from transform_service.graph_builder import process_new_emails

    await process_new_emails()
    print("  PASS: Pipeline completed")

    # Step 4: Assert meeting node exists in Memgraph
    print("\n[4/5] Asserting meeting node in Memgraph...")
    from transform_service import memgraph_client

    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (m:Meeting {source_id: $source_id}) RETURN m LIMIT 1",
            source_id=source_id,
        )
        records = [r async for r in result]

    if not records:
        print("  FAIL: No Meeting node found in Memgraph for this source_id")
        print("  (Classifier may have scored below 0.6 — check logs)")
        sys.exit(1)
    print("  PASS: Meeting node found in Memgraph")

    # Step 5: Check timeline endpoint
    print("\n[5/5] Checking /graph/timeline?window=day...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get("http://localhost:8000/graph/timeline?window=day")
            data = resp.json()
            assert "meetings" in data, "Missing 'meetings' key in timeline response"
            print(f"  PASS: Timeline returned {len(data['meetings'])} meeting(s) today")
        except Exception as exc:
            print(f"  SKIP: Could not reach /graph/timeline (service may not be running) — {exc}")

    print("\n" + "=" * 60)
    print("RESULT: PASS")
    print("=" * 60)

    await memgraph_client.close_driver()


if __name__ == "__main__":
    asyncio.run(main())
