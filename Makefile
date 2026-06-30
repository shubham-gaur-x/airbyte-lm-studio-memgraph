.PHONY: up down logs shell psql cypher test reset-db setup-memgraph smoke-test backfill tunnels setup-airbyte trigger dev-agent-logs dev-agent-trigger dev-agent-triage dev-agent-runs mcp-setup

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f transform_service

shell:
	docker compose exec transform_service bash

psql:
	docker compose exec postgres psql -U meeting_user -d meeting_memory

cypher:
	docker compose exec memgraph mgconsole

test:
	docker compose exec -w /app transform_service python -m pytest tests/ -v

reset-db:
	docker compose down -v
	docker compose up -d

setup-memgraph:
	docker compose exec transform_service python scripts/setup_memgraph.py

smoke-test:
	docker compose exec transform_service python scripts/test_pipeline.py

backfill:
	docker compose exec transform_service python scripts/backfill.py --source ALL

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

tunnels:
	@echo "=== ngrok webhook tunnel ==="
	@curl -sf http://localhost:4040/api/tunnels | python3 -c "import sys,json; t=json.load(sys.stdin)['tunnels']; [print('  webhook:', x['public_url']) for x in t]"
	@echo "=== bore postgres tunnel ==="
	@docker compose logs bore --tail=5 2>&1 | grep "listening at" | awk '{print "  postgres:", $$NF}'

setup-airbyte:
	docker compose exec transform_service python scripts/setup_airbyte.py

trigger:
	@echo "Triggering pipeline via webhook..."
	curl -s -X POST http://localhost:8000/webhook/airbyte \
	  -H 'Content-Type: application/json' \
	  -d '{"connection_id":"manual","status":"succeeded"}' | python3 -m json.tool

dev-agent-logs:
	docker compose logs -f dev_agent

dev-agent-trigger:
	curl -s -X POST http://localhost:8002/trigger/$(TICKET) | python3 -m json.tool

dev-agent-triage:
	curl -s -X POST http://localhost:8002/triage | python3 -m json.tool

dev-agent-runs:
	curl -s http://localhost:8002/runs | python3 -m json.tool

mcp-setup:
	@echo "Installing Jira MCP server..."
	npm install -g @aashari/mcp-server-atlassian-jira
	@echo "Writing Claude Desktop MCP config..."
	@python3 scripts/update_claude_mcp_config.py
	@echo "Done. Restart Claude Desktop to pick up the new MCP servers."
