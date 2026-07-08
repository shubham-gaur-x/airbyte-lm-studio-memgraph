"""One-off: create the Jira connector in the app.airbyte.ai workspace (hosted mode).

Chain: bearer auth -> resolve workspace -> ensure DIRECT-mode Jira source
template -> create connector (Token credentials + domain environment).
Idempotent: reuses an existing Jira connector/template when present.

Reads all values from env; prints only identifiers, never credentials.

Run:
  docker compose exec transform_service python scripts/create_agents_jira_connector.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

from airbyte_agent_sdk.cloud_utils.client import AirbyteCloudClient

JIRA_SOURCE_DEFINITION_ID = "68e63de2-bb83-4c7e-93fa-a8a9051e3993"


async def main() -> None:
    client = AirbyteCloudClient(
        client_id=os.environ["AIRBYTE_AGENTS_CLIENT_ID"],
        client_secret=os.environ["AIRBYTE_AGENTS_CLIENT_SECRET"],
        organization_id=os.environ.get("AIRBYTE_AGENTS_ORGANIZATION_ID") or None,
    )
    token = await client.get_bearer_token()
    print("auth: bearer token acquired")
    headers = client._build_headers(token=token)
    http = client._http_client
    base = client.API_BASE_URL

    # 1. Workspace
    resp = await http.get(f"{base}/api/v1/workspaces", headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    workspaces = payload.get("data") or payload.get("workspaces") or payload
    if isinstance(workspaces, dict):
        workspaces = [workspaces]
    ws_name = workspaces[0].get("name") or workspaces[0].get("workspace_name")
    print(f"workspace: {ws_name}")

    # 2. Existing connector? (idempotency)
    resp = await http.get(
        f"{base}/api/v1/integrations/connectors",
        params={"workspace_name": ws_name},
        headers=headers,
    )
    resp.raise_for_status()
    for conn in resp.json().get("data") or []:
        blob = str(conn).lower()
        if "jira" in blob:
            print(f"existing Jira connector: id={conn.get('id')}")
            print(f"CONNECTOR_ID={conn.get('id')}")
            return

    # 3. Ensure a DIRECT-mode Jira source template
    resp = await http.get(f"{base}/api/v1/integrations/templates/sources", headers=headers)
    resp.raise_for_status()
    tdata = resp.json()
    templates = tdata.get("data") or tdata.get("templates") or []
    template_id = None
    for t in templates if isinstance(templates, list) else []:
        if "jira" in str(t).lower():
            template_id = t.get("id")
            print(f"existing Jira template: id={template_id}")
            break
    if template_id is None:
        resp = await http.post(
            f"{base}/api/v1/integrations/templates/sources",
            json={
                "actor_definition_id": JIRA_SOURCE_DEFINITION_ID,
                "name": "jira",
                "mode": "DIRECT",
                "partial_default_config": {},
            },
            headers=headers,
        )
        if resp.status_code >= 400:
            print(f"TEMPLATE CREATE FAILED: HTTP {resp.status_code}: {resp.text[:500]}")
            sys.exit(1)
        tj = resp.json()
        template_id = tj.get("id") or (tj.get("data") or {}).get("id")
        print(f"created Jira source template: id={template_id} (mode=DIRECT)")

    # 4. Create the connector
    # Only ONE of connector_type / definition_id / source_template_id is allowed
    body = {
        "workspace_name": ws_name,
        "name": "meeting-memory-jira",
        "source_template_id": template_id,
        "credentials": {
            "username": os.environ["JIRA_EMAIL"],
            "password": os.environ["JIRA_API_TOKEN"],
        },
        # domain lives in the template's partial_default_config (set at template
        # creation/patch time) — the create call only supplies credentials.
    }
    resp = await http.post(
        f"{base}/api/v1/integrations/connectors", json=body, headers=headers
    )
    if resp.status_code >= 400:
        print(f"CREATE FAILED: HTTP {resp.status_code}: {resp.text[:500]}")
        sys.exit(1)
    created = resp.json()
    connector_id = created.get("id") or (created.get("data") or {}).get("id")
    print(f"created Jira connector: id={connector_id}")
    print(f"CONNECTOR_ID={connector_id}")


if __name__ == "__main__":
    asyncio.run(main())
