#!/usr/bin/env python3
"""
Auto-update all Airbyte Postgres destinations with the current bore.pub port.

Usage:
    AIRBYTE_CLIENT_ID=<id> AIRBYTE_CLIENT_SECRET=<secret> python scripts/update_bore_port.py [--sync]

Get credentials (works on trial accounts — this is the OAuth2 "Application"
mechanism, separate from the paid-only personal API key page):
    cloud.airbyte.com → Settings → Applications → New Application
    Copy the client_id and client_secret shown once on creation.

Add them to .env as AIRBYTE_CLIENT_ID / AIRBYTE_CLIENT_SECRET and they'll be
picked up automatically. Pass --sync to also trigger a manual sync on all
active connections after updating ports.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

AIRBYTE_AUTH_URL = "https://api.airbyte.com/v1/applications/token"
AIRBYTE_BASE = "https://api.airbyte.com/v1"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_bore_port() -> int:
    # Use docker logs directly (no tail limit) so we always catch the last
    # "listening" line even after many proxy-connection lines have pushed it out.
    result = subprocess.run(
        ["docker", "logs", "airbyte-lm-studio-memgraph-bore-1"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    matches = re.findall(r"listening at bore\.pub:(\d+)", output, re.IGNORECASE)
    if not matches:
        sys.exit("bore tunnel not running — start it with: docker compose up bore -d")
    port = int(matches[-1])
    print(f"Current bore port: {port}")
    return port


async def get_access_token(client: httpx.AsyncClient) -> str:
    direct_token = os.environ.get("AIRBYTE_ACCESS_TOKEN", "").strip()
    if direct_token:
        return direct_token

    client_id = os.environ.get("AIRBYTE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AIRBYTE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        sys.exit(
            "AIRBYTE_CLIENT_ID / AIRBYTE_CLIENT_SECRET not set.\n"
            "Trial accounts don't get personal API keys, but they do get OAuth2\n"
            "Application credentials:\n"
            "  1. cloud.airbyte.com → Settings → Applications → New Application\n"
            "  2. Copy the client_id and client_secret (shown once)\n"
            "  3. Add to .env:\n"
            "       AIRBYTE_CLIENT_ID=...\n"
            "       AIRBYTE_CLIENT_SECRET=...\n"
        )

    r = await client.post(
        AIRBYTE_AUTH_URL,
        json={"client_id": client_id, "client_secret": client_secret},
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def get_workspaces(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    r = await client.get(f"{AIRBYTE_BASE}/workspaces", headers=headers)
    r.raise_for_status()
    return r.json().get("data", [])


async def get_destinations(client: httpx.AsyncClient, headers: dict, workspace_id: str) -> list[dict]:
    r = await client.get(
        f"{AIRBYTE_BASE}/destinations", params={"workspaceId": workspace_id}, headers=headers
    )
    r.raise_for_status()
    return r.json().get("data", [])


async def update_destination_port(
    client: httpx.AsyncClient, headers: dict, dest: dict, new_port: int
) -> bool:
    dest_id = dest["destinationId"]
    name = dest.get("name", dest_id)
    dtype = dest.get("destinationType", "")

    if "postgres" not in dtype.lower():
        return False

    # Fetch current config — secrets come back masked (e.g. "**").
    # We must never send the masked value back or it overwrites the real password.
    r = await client.get(f"{AIRBYTE_BASE}/destinations/{dest_id}", headers=headers)
    r.raise_for_status()
    config = r.json().get("configuration", {})

    old_port = config.get("port")
    real_password = os.environ.get("POSTGRES_PASSWORD", "")

    port_changed = old_port != new_port
    if not port_changed and not real_password:
        print(f"  [{name}] already on port {new_port} — skipping")
        return False

    # Always inject the real password when available so the API's masked
    # placeholder ("**") never overwrites the actual stored credential.
    if real_password:
        config["password"] = real_password

    config["port"] = new_port
    r = await client.patch(
        f"{AIRBYTE_BASE}/destinations/{dest_id}",
        json={"configuration": config},
        headers=headers,
    )
    if r.status_code in (200, 204):
        print(f"  [{name}] updated {old_port} -> {new_port}")
        return True
    print(f"  [{name}] update failed: {r.status_code} {r.text[:300]}")
    return False


async def trigger_syncs(client: httpx.AsyncClient, headers: dict, workspace_id: str) -> None:
    r = await client.get(
        f"{AIRBYTE_BASE}/connections", params={"workspaceId": workspace_id}, headers=headers
    )
    r.raise_for_status()
    for conn in r.json().get("data", []):
        if conn.get("status") != "active":
            continue
        cid = conn["connectionId"]
        cname = conn.get("name", cid)
        sr = await client.post(
            f"{AIRBYTE_BASE}/jobs", json={"connectionId": cid, "jobType": "sync"}, headers=headers
        )
        if sr.status_code in (200, 201):
            print(f"  Triggered sync: {cname}")
        else:
            print(f"  Failed to trigger {cname}: {sr.status_code} {sr.text[:200]}")


async def main_async(trigger_sync: bool) -> None:
    new_port = get_bore_port()

    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_access_token(client)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        workspaces = await get_workspaces(client, headers)
        if not workspaces:
            sys.exit("No workspaces found")

        for ws in workspaces:
            ws_id = ws["workspaceId"]
            ws_name = ws.get("name", ws_id)
            print(f"\nWorkspace: {ws_name}")

            destinations = await get_destinations(client, headers, ws_id)
            updated = 0
            for dest in destinations:
                if await update_destination_port(client, headers, dest, new_port):
                    updated += 1

            print(f"Updated {updated}/{len(destinations)} Postgres destination(s)")

            if trigger_sync:
                print("Triggering syncs...")
                await trigger_syncs(client, headers, ws_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true", help="Trigger syncs after updating")
    args = parser.parse_args()
    asyncio.run(main_async(args.sync))


if __name__ == "__main__":
    main()
