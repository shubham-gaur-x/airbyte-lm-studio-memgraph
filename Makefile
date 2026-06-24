.PHONY: up down logs shell psql cypher test reset-db setup-memgraph smoke-test backfill tunnels setup-airbyte trigger

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
	docker compose exec transform_service python -m pytest -v

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
