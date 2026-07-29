#!/usr/bin/env python3
"""
One-shot Google Meet + Pub/Sub OAuth2 token capture (C1 runbook — see
docs/V5_1_EXTERNAL_WIRING_RUNBOOKS.md for the full GCP setup this depends on).

What this does:
  1. Starts a local HTTP server on port 8888 to catch Google's redirect
     (same redirect URI as scripts/refresh_gcal_token.py — reuse that OAuth
     client if it already has this URI authorized, or make a new one)
  2. Opens your browser to the Google consent screen automatically
  3. Captures the auth code (no copy-paste needed)
  4. Exchanges it for an access_token (+ refresh_token, saved for later use)
  5. Saves GOOGLE_ACCESS_TOKEN to .env

Known limitation: meet_ingest.py reads GOOGLE_ACCESS_TOKEN directly and does not yet
auto-refresh it — Google expires access tokens in ~1 hour, so re-run this script when
transform_service logs start showing meet_ingest failures again. The refresh_token is
saved as GOOGLE_REFRESH_TOKEN for a future auto-refresh implementation, not yet built.

One-time redirect URI setup (Google Cloud Console), if not already done for gcal:
  APIs & Services → Credentials → edit your OAuth client →
  Authorized redirect URIs → Add: http://localhost:8888/callback

Usage:
  python scripts/refresh_meet_token.py
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
SCOPES = " ".join([
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/pubsub",
])

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


async def main() -> None:
    global _server

    # Reuse the Calendar OAuth client if a Meet-specific one isn't set — same GCP project,
    # same redirect URI, just needs the Meet + Pub/Sub scopes added to it in the console.
    client_id = (os.environ.get("MEET_CLIENT_ID") or os.environ.get("GCAL_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("MEET_CLIENT_SECRET") or os.environ.get("GCAL_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        sys.exit(
            "No OAuth client configured. Set MEET_CLIENT_ID/MEET_CLIENT_SECRET in .env, "
            "or GCAL_CLIENT_ID/GCAL_CLIENT_SECRET if reusing the Calendar OAuth client "
            "(make sure Meet + Pub/Sub scopes are enabled for it in Google Cloud Console)."
        )

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
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        })
    )

    _server = HTTPServer(("localhost", 8888), _CallbackHandler)
    t = threading.Thread(target=_server.serve_forever, daemon=True)
    t.start()

    _p("Opening browser for Google Meet + Pub/Sub authorization...")
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
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        _p(f"Response from Google: {tokens}")
        sys.exit("No access_token in response.")

    set_key(ENV_PATH, "GOOGLE_ACCESS_TOKEN", access_token)
    _p(f"Saved GOOGLE_ACCESS_TOKEN to .env (expires in ~{tokens.get('expires_in', 3600)}s)")

    if refresh_token:
        set_key(ENV_PATH, "GOOGLE_REFRESH_TOKEN", refresh_token)
        _p("Saved GOOGLE_REFRESH_TOKEN to .env (for a future auto-refresh implementation).")

    _p("\nDone. Recreate the container to pick this up:")
    _p("  docker compose up -d --force-recreate transform_service")
    _p("\nRe-run this script when the access token expires (~1hr) until auto-refresh is built.")


if __name__ == "__main__":
    asyncio.run(main())
