#!/usr/bin/env python3
"""
Configure Airbyte Cloud destination + webhook to point at the local stack.

Usage:
    AIRBYTE_CLIENT_ID=<id> AIRBYTE_CLIENT_SECRET=<secret> python scripts/setup_airbyte.py

Get credentials:
    cloud.airbyte.com → Settings → Applications → New Application
    Copy the client_id and client_secret shown once on creation.

What this script does:
    1. Gets a fresh access token via client credentials
    2. Finds or creates the local Postgres destination
    3. Updates destination to ngrok TCP endpoint
    4. Lists connections and prints their IDs
    5. Prints the webhook URL to set in each connection
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

AIRBYTE_AUTH_URL = "https://api.airbyte.com/v1/applications/token"
AIRBYTE_BASE = "https://api.airbyte.com/v1"
WEBHOOK_PUBLIC_URL = "https://chemist-retiree-squash.ngrok-free.dev/webhook/airbyte"
WORKSPACE_SHORT_ID = "ae67dfe0"


async def get_access_token(client: httpx.AsyncClient) -> str:
    client_id = os.environ.get("AIRBYTE_CLIENT_ID")
    client_secret = os.environ.get("AIRBYTE_CLIENT_SECRET")
    # Also accept a pre-fetched token directly
    direct_token = os.environ.get("AIRBYTE_ACCESS_TOKEN")
    if direct_token:
        return direct_token
    if not client_id or not client_secret:
        print("ERROR: Set AIRBYTE_CLIENT_ID and AIRBYTE_CLIENT_SECRET (or AIRBYTE_ACCESS_TOKEN).")
        print()
        print("To get credentials:")
        print("  1. Go to cloud.airbyte.com → Settings → Applications")
        print("  2. Click 'New Application'")
        print("  3. Copy the client_id and client_secret")
        print()
        print("Then run:")
        print("  AIRBYTE_CLIENT_ID=xxx AIRBYTE_CLIENT_SECRET=yyy python scripts/setup_airbyte.py")
        sys.exit(1)
    r = await client.post(
        AIRBYTE_AUTH_URL,
        json={"client_id": client_id, "client_secret": client_secret},
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def get_bore_tcp_endpoint() -> str | None:
    """Read bore port from bore container logs."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["docker", "compose", "logs", "bore", "--tail=20"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            text=True,
            stderr=subprocess.STDOUT,
        )
        for line in out.splitlines():
            if "listening at bore.pub:" in line.lower():
                return "bore.pub:" + line.split("bore.pub:")[-1].strip()
    except Exception as exc:
        print(f"  WARNING: Could not read bore logs: {exc}")
    return None


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        print("Getting Airbyte access token...")
        token = await get_access_token(client)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        print("  OK")

        # 1. Find workspace
        print("\nFetching workspace...")
        r = await client.get(f"{AIRBYTE_BASE}/workspaces", headers=headers)
        r.raise_for_status()
        workspaces = r.json().get("data", [])
        if not workspaces:
            print("No workspaces found.")
            sys.exit(1)
        workspace = workspaces[0]
        workspace_id = workspace["workspaceId"]
        print(f"  {workspace.get('name', 'unknown')} ({workspace_id})")

        # 2. Get bore TCP endpoint
        print("\nReading bore TCP tunnel...")
        bore_endpoint = await get_bore_tcp_endpoint()
        if bore_endpoint:
            ngrok_host, ngrok_port_str = bore_endpoint.rsplit(":", 1)
            ngrok_port = int(ngrok_port_str)
            print(f"  TCP endpoint: {bore_endpoint}")
        else:
            print("  Could not detect bore TCP endpoint.")
            print("  Is bore running? (docker compose logs bore)")
            print("  Continuing with manual placeholder — update destination manually after.")
            ngrok_host = "bore.pub"
            ngrok_port = 0

        # 3. List destinations
        print("\nFetching destinations...")
        r = await client.get(
            f"{AIRBYTE_BASE}/destinations",
            params={"workspaceId": workspace_id},
            headers=headers,
        )
        r.raise_for_status()
        destinations = r.json().get("data", [])

        postgres_dest = None
        for d in destinations:
            name = d.get("name", "")
            dtype = d.get("destinationType", "")
            print(f"  - {name} ({dtype}) [{d['destinationId']}]")
            if "postgres" in dtype.lower() or "local" in name.lower() or "postgres" in name.lower():
                postgres_dest = d

        # 4. Update or report on Postgres destination
        if postgres_dest:
            dest_id = postgres_dest["destinationId"]
            print(f"\nFound Postgres destination: {postgres_dest['name']} ({dest_id})")
            if ngrok_host != "REPLACE_WITH_NGROK_HOST":
                print(f"Updating → {ngrok_host}:{ngrok_port} ...")
                r = await client.patch(
                    f"{AIRBYTE_BASE}/destinations/{dest_id}",
                    json={
                        "configuration": {
                            "destinationType": "postgres",
                            "host": ngrok_host,
                            "port": ngrok_port,
                            "database": "meeting_memory",
                            "username": "meeting_user",
                            "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
                            "ssl": False,
                            "schema": "public",
                        }
                    },
                    headers=headers,
                )
                if r.status_code in (200, 204):
                    print(f"  Destination updated to {ngrok_host}:{ngrok_port}")
                else:
                    print(f"  Update failed ({r.status_code}): {r.text[:400]}")
        else:
            print("\nNo Postgres destination found. You need to create one in Airbyte Cloud:")
            print(f"  Host:     {ngrok_host}")
            print(f"  Port:     {ngrok_port}")
            print("  Database: meeting_memory")
            print("  Username: meeting_user")
            print("  Password: changeme  (from .env POSTGRES_PASSWORD)")
            print("  SSL:      disabled")

        # 5. List connections
        print("\nFetching connections...")
        r = await client.get(
            f"{AIRBYTE_BASE}/connections",
            params={"workspaceId": workspace_id},
            headers=headers,
        )
        r.raise_for_status()
        connections = r.json().get("data", [])

        print(f"\nFound {len(connections)} connection(s):")
        for c in connections:
            print(f"  {c.get('name','unnamed')} ({c['connectionId']}) — {c.get('status','?')}")

        # 6. Trigger a sync on each active connection
        if connections:
            print("\nTriggering sync on all active connections...")
            for c in connections:
                if c.get("status") == "active":
                    r = await client.post(
                        f"{AIRBYTE_BASE}/jobs",
                        json={"connectionId": c["connectionId"], "jobType": "sync"},
                        headers=headers,
                    )
                    if r.status_code in (200, 201):
                        job = r.json()
                        print(f"  Sync started: {c.get('name')} → job {job.get('jobId')}")
                    else:
                        print(f"  Sync failed for {c.get('name')}: {r.status_code} {r.text[:200]}")

        print(f"\n{'='*60}")
        print("SETUP COMPLETE")
        print(f"{'='*60}")
        print(f"Postgres endpoint:  {ngrok_host}:{ngrok_port}")
        print(f"Webhook URL:        {WEBHOOK_PUBLIC_URL}")
        print(f"Health check:       {WEBHOOK_PUBLIC_URL.replace('/webhook/airbyte', '/health')}")
        print()
        print("MANUAL STEP — set webhook on each Airbyte connection:")
        print("  Connection → Settings → Notifications → Webhook")
        print(f"  URL: {WEBHOOK_PUBLIC_URL}")


if __name__ == "__main__":
    asyncio.run(main())
