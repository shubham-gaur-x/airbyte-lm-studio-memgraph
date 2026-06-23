"""Run once before first use to create Memgraph constraints and indexes."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

from transform_service import memgraph_client


async def main() -> None:
    print("Setting up Memgraph constraints and indexes...")
    await memgraph_client.create_indexes()
    print("Done. Memgraph is ready.")
    await memgraph_client.close_driver()


if __name__ == "__main__":
    asyncio.run(main())
