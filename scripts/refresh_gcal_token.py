#!/usr/bin/env python3
"""
One-shot Google Calendar OAuth2 token refresh.

What this does:
  1. Starts a local HTTP server on port 8888 to catch Google's redirect
  2. Opens your browser to the Google consent screen automatically
  3. Captures the auth code (no copy-paste needed)
  4. Exchanges it for a refresh_token
  5. Saves the refresh_token to .env
  6. Patches the Airbyte gcal-source directly via API

One-time redirect URI setup (Google Cloud Console):
  APIs & Services → Credentials → edit your OAuth client →
  Authorized redirect URIs → Add: http://localhost:8888/callback

Usage:
  python scripts/refresh_gcal_token.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
from dotenv import load_dotenv, set_key

load_dotenv()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://localhost:8888/callback"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

AIRBYTE_AUTH_URL = "https://api.airbyte.com/v1/applications/token"
AIRBYTE_BASE = "https://api.airbyte.com/v1"
GCAL_SOURCE_ID = "4a61c230-bd66-4360-949f-e9ba69e331be"

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

_auth_code: str | None = None
_code_event = threading.Event()
_server: HTTPServer | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
            return

        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:40px'>"
                b"<h2>Authorization successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
            _code_event.set()
            # Shut down server from a new thread to avoid deadlock
            threading.Thread(target=_server.shutdown, daemon=True).start()
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"OAuth error: {error}".encode())
            _code_event.set()
            threading.Thread(target=_server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress access logs


def _p(msg: str) -> None:
    print(msg, flush=True)


async def _exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        return r.json()


async def _patch_airbyte_source(refresh_token: str, client_id: str, client_secret: str) -> None:
    airbyte_cid = os.environ.get("AIRBYTE_CLIENT_ID", "").strip()
    airbyte_csec = os.environ.get("AIRBYTE_CLIENT_SECRET", "").strip()
    if not airbyte_cid or not airbyte_csec:
        _p("  WARNING: AIRBYTE_CLIENT_ID/SECRET not set — skipping Airbyte update.")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            AIRBYTE_AUTH_URL,
            json={"client_id": airbyte_cid, "client_secret": airbyte_csec},
        )
        r.raise_for_status()
        hdrs = {"Authorization": f"Bearer {r.json()['access_token']}"}

        sr = await client.get(f"{AIRBYTE_BASE}/sources/{GCAL_SOURCE_ID}", headers=hdrs)
        sr.raise_for_status()
        config = sr.json().get("configuration", {})
        # Always set all credential fields from env/args — never rely on masked API values
        config["client_id"] = client_id
        config["client_secret"] = client_secret
        config["client_refresh_token_2"] = refresh_token

        pr = await client.patch(
            f"{AIRBYTE_BASE}/sources/{GCAL_SOURCE_ID}",
            json={"configuration": config},
            headers=hdrs,
        )
        if pr.status_code in (200, 204):
            _p("  gcal-source patched in Airbyte.")
        else:
            _p(f"  Airbyte patch failed ({pr.status_code}): {pr.text[:400]}")


async def main() -> None:
    global _server

    client_id = os.environ.get("GCAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GCAL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        sys.exit("GCAL_CLIENT_ID / GCAL_CLIENT_SECRET not set in .env")

    # Check port is free before we start
    import socket as _sock
    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as probe:
        probe.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        try:
            probe.bind(("localhost", 8888))
        except OSError:
            sys.exit("Port 8888 is busy from a previous run.\nFix: lsof -ti:8888 | xargs kill -9")

    auth_url = (
        GOOGLE_AUTH_URL + "?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        })
    )

    _server = HTTPServer(("localhost", 8888), _CallbackHandler)
    # serve_forever handles multiple requests (favicon, actual callback)
    t = threading.Thread(target=_server.serve_forever, daemon=True)
    t.start()

    _p("Opening browser for Google Calendar authorization...")
    _p("If it doesn't open automatically, visit:")
    _p(f"  {auth_url}\n")
    webbrowser.open(auth_url)
    _p("Waiting for you to authorize in the browser...")

    if not _code_event.wait(timeout=120):
        sys.exit("Timed out waiting for authorization (120s). Run again.")

    if not _auth_code:
        sys.exit("Authorization was denied or cancelled.")

    _p("Auth code received. Exchanging for tokens...")
    tokens = await _exchange_code(_auth_code, client_id, client_secret)
    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        _p(f"Response from Google: {tokens}")
        sys.exit(
            "No refresh_token in response.\n"
            "Ensure the OAuth client type is 'Web application' in Google Cloud Console."
        )

    _p(f"Got refresh token (length={len(refresh_token)})")

    set_key(ENV_PATH, "GCAL_REFRESH_TOKEN", refresh_token)
    _p("Saved GCAL_REFRESH_TOKEN to .env")

    _p("Updating Airbyte gcal-source...")
    await _patch_airbyte_source(refresh_token, client_id, client_secret)

    _p("\nDone! Token is saved — won't expire unless Google explicitly revokes it.")


if __name__ == "__main__":
    asyncio.run(main())
