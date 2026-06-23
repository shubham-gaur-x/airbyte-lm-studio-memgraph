# Demo Guide — airbyte-lm-studio-memgraph v4

10-minute demo flow for Matteo and the Airbyte team.

---

## Prerequisites Checklist (Do Before Demo)

```
[ ] LM Studio running on Mac with gemma3:12b loaded and green checkmark
[ ] ngrok TCP tunnel running (postgres:5432 exposed for Airbyte)
[ ] docker compose up — all 4 services green
[ ] make health → all three: lm_studio=true, memgraph=true, postgres=true
[ ] Airbyte Cloud: last sync < 24h ago (or trigger manual sync beforehand)
[ ] Memgraph Lab open at http://localhost:3000
[ ] Terminal with logs ready: make logs
```

---

## Demo Flow (10 minutes)

### [0:00] Setup Slide / Intro (1 min)

> "This is v4 of a meeting-memory pipeline I've been building. Everything you're about to see runs locally on my Mac. No cloud infrastructure at demo time — except Airbyte, which I'm keeping because it's the right tool for the ingestion job."

### [1:00] Show Airbyte Cloud (2 min)

1. Open [cloud.airbyte.com](https://cloud.airbyte.com)
2. Show the 3 connectors: Gmail, Google Calendar, Jira
3. Point to last sync times — incremental, Append+Dedup
4. Trigger a **manual sync** on Gmail connector
5. Switch to terminal: `make logs`

> "Airbyte is doing what it does best — normalizing and syncing data from three different sources. The destination is my local Postgres, exposed via ngrok."

### [3:00] Watch the Pipeline Fire (2 min)

In the terminal logs, show:

1. Webhook fires when sync completes: `webhook.queued connection_id=...`
2. Classifier scores the email: `graph_builder.email_processed score=0.72`
3. LM Studio extraction: `extractor.success duration_ms=4200 confidence=0.87`
4. Graph write: `memgraph.meeting_upserted attendees=3 topics=4 actions=2`
5. Jira push (if action items): `jira_pusher.issue_created key=SCRUM-42`

> "The classifier filters noise — newsletters, promos — before we spend any inference compute. Then LM Studio runs locally on Gemma3:12b. No data leaves this Mac."

### [5:00] Memgraph Lab — Graph Visualization (2 min)

1. Open [localhost:3000](http://localhost:3000)
2. Run: `MATCH (m:Meeting)-[:ATTENDED]-(p:Person) RETURN m, p LIMIT 20`
3. Show the graph — meetings connected to people, topics, decisions
4. Run: `MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic) RETURN m, t LIMIT 30`

> "This is the core value — relationships between meetings, people, and topics that flat storage can't represent. Who attended what, what topics keep coming up, which decisions led to which action items."

### [7:00] API + Timeline View (1 min)

```bash
# Timeline — last 7 days
curl http://localhost:8000/graph/timeline?window=week | python3 -m json.tool

# Open action items
curl http://localhost:8000/graph/actions/open | python3 -m json.tool

# Weekly digest
curl http://localhost:8000/graph/digest/weekly | python3 -m json.tool
```

### [8:00] Claude Desktop via MCP (1 min)

In Claude Desktop (configured with Memgraph MCP server):

> "What meetings happened this week and what decisions were made?"

> "Show me all open action items for sarah@example.com"

> "Which topics have come up most in the last month?"

> "This is the MCP server running as a Docker sidecar. Any AI agent — Claude, a custom agent, whatever — can query the meeting memory graph in natural language."

### [9:00] Jira Bidirectional Loop (1 min)

1. Open Jira at shubhamgaur1.atlassian.net — show SCRUM board with auto-created tasks
2. Mark one task as Done in Jira
3. Explain: next Airbyte sync → `raw_jira_issues` → `jira_agent.py` → `ActionItem.done=True` in Memgraph

> "This is the part most people miss. We write TO Jira and read FROM Jira. The graph always reflects real Jira state."

---

## Key Talking Points

| Component | Talking Point |
|---|---|
| Airbyte | "Best-in-class connectors, incremental sync, webhook triggering. This is what Airbyte is for." |
| LM Studio | "Local Gemma3:12b. 4-5 seconds per extraction. Zero data leaves the Mac." |
| Memgraph | "Graph relationships vs flat rows. Querying who attended what meeting and what they decided would be multiple JOINs in SQL. One Cypher query here." |
| MCP server | "Any AI agent can now query this graph. Claude, custom agents, whatever connects over MCP." |
| Timeline view | "See how topics and decisions evolved over time — day/week/month windows." |
| Jira loop | "Write AND read. Full bidirectional sync via Airbyte's Jira source connector." |
| ACID transactions | "Every multi-node write is atomic. If Jira fails after a graph write, the graph write stands. No partial state." |

---

## Fallback Plans

**LM Studio is slow (> 10s per extraction):**
- Use pre-extracted sample: `python scripts/test_pipeline.py` runs a cached sample
- Keep `sample_data/sample_extracted.json` ready with a pre-extracted meeting
- Acknowledge: "Gemma3 12B on M2 Pro — for production I'd run on a GPU box, but this is fully local on 16GB RAM"

**Memgraph has no data yet:**
- Run: `make backfill` to reprocess all historical records
- Or: `make smoke-test` to inject sample data

**Airbyte sync fails:**
- Trigger via API: `curl -X POST http://localhost:8000/webhook/airbyte -H 'Content-Type: application/json' -d '{"connection_id":"demo","status":"succeeded"}'`
- This fires the pipeline manually without needing a real Airbyte sync
