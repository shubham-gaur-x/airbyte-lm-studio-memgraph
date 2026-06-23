# Airbyte Cloud Setup Guide

How to point existing Airbyte Cloud connectors at local Postgres via ngrok.

---

## Prerequisites

- Airbyte Cloud workspace `ae67dfe0` with Gmail, Google Calendar, and Jira connectors already configured
- ngrok account (free tier is fine) — [dashboard.ngrok.com](https://dashboard.ngrok.com)
- Local stack running: `make up` && `make health`

---

## Step 1: Create a Static ngrok TCP Address (one-time)

1. Go to [dashboard.ngrok.com → Cloud Edge → TCP Addresses](https://dashboard.ngrok.com/cloud-edge/tcp-addresses)
2. Click **New Address** → copy the static hostname (e.g. `4.tcp.ngrok.io`) and port (e.g. `12345`)
3. Save these — you'll use them every time

## Step 2: Start the ngrok Tunnel

```bash
ngrok tcp 5432 --url your-static-domain.tcp.ngrok.io
```

Keep this terminal running during syncs. Verify it's active:

```bash
curl http://localhost:4040/api/tunnels | python3 -m json.tool
```

## Step 3: Update Airbyte Cloud Destination

1. Log in to [cloud.airbyte.com](https://cloud.airbyte.com)
2. Go to **Workspace ae67dfe0 → Destinations → Postgres (your local destination)**
3. Edit the destination:
   - **Host:** your-static-domain.tcp.ngrok.io
   - **Port:** 12345 (your ngrok TCP port)
   - **Database:** `meeting_memory`
   - **Username:** `meeting_user`
   - **Password:** value from your `.env` `POSTGRES_PASSWORD`
   - **SSL:** disabled
4. Click **Test connection** — should show green
5. Save

## Step 4: Verify Connectors Still Sync

Trigger a manual sync on each connector and verify data lands in local Postgres:

```bash
# Gmail
make psql
SELECT COUNT(*) FROM raw_emails;

# Google Calendar
SELECT COUNT(*) FROM raw_calendar_events;

# Jira
SELECT COUNT(*) FROM raw_jira_issues;
```

## Step 5: Update Webhook URL

So Airbyte notifies your transform service on sync completion:

1. Airbyte Cloud → **Connections → [any connection] → Settings → Notifications → Webhook**
2. Set webhook URL to: `http://<your-public-ip>:8000/webhook/airbyte`

> **For demo only:** Expose port 8000 temporarily with ngrok HTTP:
> ```bash
> ngrok http 8000
> ```
> Use the HTTPS URL shown (e.g. `https://abc123.ngrok.io/webhook/airbyte`)

---

## Operational Notes

- ngrok must be running whenever Airbyte syncs
- Schedule Airbyte syncs during demo prep windows
- Free ngrok: 1 simultaneous tunnel. For both Postgres (TCP) and webhook (HTTP), use a paid plan or alternate between them
- The static TCP address persists even if the tunnel is stopped and restarted
