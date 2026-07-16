"""Phase 32 schema migration runner — idempotent, safe to re-run.

Adds Ticket/PullRequest/Team/Project constraints, backfills provenance defaults on
extracted nodes, and creates (:Meeting)-[:MENTIONS]->(:Ticket) edges from ticket keys
found in meeting text. All Cypher lives in memgraph_client.migrate_schema_v5; this is a
thin one-off runner (like scripts/setup_memgraph.py).

    docker exec airbyte-lm-studio-memgraph-transform_service-1 python -m scripts.migrate_schema_v5
"""
from __future__ import annotations

import asyncio

from transform_service import memgraph_client


async def main() -> None:
    counts = await memgraph_client.migrate_schema_v5()
    print("migrate_schema_v5 result:", counts)
    await memgraph_client.close_driver()


if __name__ == "__main__":
    asyncio.run(main())
