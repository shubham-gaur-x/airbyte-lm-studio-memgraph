# v5.1 External Wiring Runbooks (C1–C3)

These three items need credentials or actions only you can grant — I've built the code
they plug into (P1 transcript capture, P3 entity resolution, P2 GitHub webhook), each
already a safe no-op without them. Nothing here has been wired live; these are the exact
steps to do it when you're ready.

---

## C1 — Google Meet transcript capture (P1)

**What's already built:** `transform_service/meet_ingest.py` (Pub/Sub-pull consumer + Meet
REST fetch) and `main._poll_meet_transcripts` (runs every 5 min, currently a no-op —
`meet_ingest.pull_and_stage` returns 0 immediately when creds are unset).

**What you need to do (one-time GCP setup):**

1. **GCP project + APIs.** In an existing or new GCP project, enable:
   - Google Meet API
   - Google Workspace Events API
   - Cloud Pub/Sub API

2. **Pub/Sub topic + PULL subscription** (no inbound tunnel — matches the fully-local
   principle):
   ```bash
   gcloud pubsub topics create meet-transcripts
   gcloud pubsub subscriptions create meet-transcripts-pull --topic=meet-transcripts
   ```
   `MEET_PUBSUB_SUBSCRIPTION` in `.env` is the full resource name:
   `projects/<your-project-id>/subscriptions/meet-transcripts-pull`

3. **Workspace Events subscription** — subscribes the topic above to
   `google.workspace.meet.transcript.v2.fileGenerated`. This needs a Workspace admin
   (domain-wide) or a user-level subscription depending on your org's Meet setup; see
   [Google's Meet transcript events guide](https://developers.google.com/workspace/events/guides/events-meet).

4. **OAuth client** (if you don't already have one — `refresh_gcal_token.py` may have set
   one up for Calendar already; you can reuse it):
   - GCP Console → APIs & Services → Credentials → OAuth client ID → Desktop app
   - Authorized redirect URI: `http://localhost:8888/callback`

5. **Get an access token.** Run the helper script (mirrors the existing
   `scripts/refresh_gcal_token.py` pattern):
   ```bash
   python scripts/refresh_meet_token.py
   ```
   This opens a browser consent screen for the Meet + Pub/Sub scopes and writes
   `GOOGLE_ACCESS_TOKEN` to `.env`.

   **Known limitation (by design, not yet built):** this writes a raw access token, which
   Google expires in ~1 hour. `meet_ingest.py` does not yet auto-refresh it — re-run the
   script when it expires. A refresh-token + auto-refresh loop is a reasonable v5.2
   follow-up once you've confirmed the capture path works end-to-end; I didn't build it
   speculatively without you validating the manual path first.

6. **Recreate the container** so it picks up the new env vars:
   ```bash
   docker compose up -d --force-recreate transform_service
   ```
   Confirm it's live: `docker compose logs transform_service | grep meet_ingest` should
   stop showing `meet_ingest.disabled` on the next 5-minute tick.

---

## C2 — P3 canonical people roster

**What's already built:** `transform_service/person_resolver.py` reads `PERSON_ROSTER_PATH`
(a JSON file) at ingestion time. Empty/unset today, so entity resolution runs
deterministic-email-only + fuzzy-match-against-existing-Person-nodes, with no canonical
roster to match against yet.

**Option A — static file (fastest, no new scopes):**

1. Copy the template:
   ```bash
   cp sample_data/roster.example.json sample_data/roster.json
   ```
2. Fill in your real attendees — one entry per canonical person:
   ```json
   [
     {"name": "Matteo Vaiente", "email": "matteo@onixnet.com", "aliases": ["m.vaiente@onixnet.com"], "tracked": true},
     {"name": "Shubham Gaur", "email": "shubham.gaur@onixnet.com", "aliases": [], "tracked": false}
   ]
   ```
   `tracked: true` opts a person into PageRank/centrality rankings (governance gate from
   P3 — default `false`, per your earlier decision to keep per-person analytics opt-in).
3. Set `PERSON_ROSTER_PATH=/app/sample_data/roster.json` in `.env` (the container path,
   since `sample_data/` is not currently volume-mounted — check `docker-compose.yml` if you
   want to mount it live instead of rebuilding).
4. Recreate: `docker compose up -d --force-recreate transform_service`.

**Option B — Google Workspace Directory sync (more setup, stays current automatically):**
Not built — would need the Admin SDK Directory API (`admin.directory.user.readonly` scope,
domain-wide delegation) to pull your org's roster and write it to the same JSON shape on a
schedule. Flagging as a real option, not building it speculatively — say the word if you
want it and I'll build it against your actual Workspace setup rather than guessing at scopes
you may not have granted.

---

## C3 — GitHub webhook registration (P2)

**What's already built:** `transform_service/github_webhook.py` +
`POST /webhook/github`, HMAC-verified against `GITHUB_WEBHOOK_SECRET` (unset = accept, dev
default — set it before exposing this publicly).

**What you need to do:**

1. **Expose transform_service publicly.** This repo already solved this exact problem for
   Postgres via the `bore` service (`docker-compose.yml`, forwards local 5432 to
   `bore.pub`). The same pattern works for port 8000 — either extend the existing `bore`
   container with a second `bore local 8000 --to bore.pub` process, or add a second bore
   service. I didn't add this myself since it changes what's publicly reachable, which
   felt like something you should explicitly opt into rather than me silently exposing a
   port.

2. **Generate and set a webhook secret:**
   ```bash
   openssl rand -hex 32
   ```
   Put the result in `.env` as `GITHUB_WEBHOOK_SECRET`, then
   `docker compose up -d --force-recreate transform_service`.

3. **Register the webhook** (once you have a public URL from step 1):
   ```bash
   gh api repos/shubham-gaur-x/airbyte-lm-studio-memgraph/hooks \
     --method POST \
     -f name=web \
     -f "config[url]=https://<your-bore-url>/webhook/github" \
     -f "config[content_type]=json" \
     -f "config[secret]=<the secret from step 2>" \
     -F "events[]=pull_request" \
     -F "events[]=push" \
     -F "events[]=check_suite"
   ```

4. **Verify:** merge or push to an `agent/<KEY>` branch and check
   `docker compose logs transform_service | grep webhook.github` for the
   `webhook.github.queued` log line, then confirm via
   `curl http://localhost:8000/graph/provenance/by-ticket/<KEY>` that `Commit`/`FileChange`
   nodes landed.
