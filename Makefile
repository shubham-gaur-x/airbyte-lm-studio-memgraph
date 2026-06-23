.PHONY: up down logs shell psql cypher test reset-db setup-memgraph smoke-test backfill

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
