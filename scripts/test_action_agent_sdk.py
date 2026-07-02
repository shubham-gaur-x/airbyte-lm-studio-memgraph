"""Manual probe: verify Airbyte Agent SDK credentials and Jira connector.

Run after configuring the Jira connector and SDK credentials in app.airbyte.ai:
  docker compose exec transform_service python scripts/test_action_agent_sdk.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

from airbyte_agent_sdk.connectors.jira import JiraConnector
from airbyte_agent_sdk.types import AirbyteAuthConfig


async def main() -> None:
    auth = AirbyteAuthConfig(
        airbyte_client_id=os.environ["AIRBYTE_AGENTS_CLIENT_ID"],
        airbyte_client_secret=os.environ["AIRBYTE_AGENTS_CLIENT_SECRET"],
        connector_id=os.environ["AIRBYTE_AGENTS_CONNECTOR_ID"],
    )
    project = os.environ.get("JIRA_PROJECT_KEY", "SCRUM")
    async with JiraConnector(auth_config=auth) as jira:
        result = await jira.issues.api_search(
            jql=f'project = "{project}" ORDER BY created DESC',
            max_results=3,
            fields="summary,status,labels",
        )
        records = getattr(result, "data", result)
        print("SDK connectivity OK. Sample issues:")
        for r in list(records)[:3]:
            print(" -", r)


if __name__ == "__main__":
    asyncio.run(main())
