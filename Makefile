# JARVIS X — common tasks. Run `make help` for the list.
.DEFAULT_GOAL := help
COMPOSE := docker compose -f infra/compose/docker-compose.dev.yml

.PHONY: help bootstrap up down db-shell migrate revision api worker scheduler video-worker stack tunnel aws-launch deploy prod-up prod-logs prod-down test test-live seed demo-reset connect-google connectors mobile \
        mobile-test openapi lint fmt clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-time setup: pin Python, install deps, start Postgres, migrate
	uv python pin 3.12
	uv sync --extra dev
	$(COMPOSE) up -d
	@echo "waiting for postgres..."
	@until $(COMPOSE) exec -T postgres pg_isready -U jarvis -d jarvis >/dev/null 2>&1; do sleep 1; done
	@$(COMPOSE) exec -T postgres psql -U jarvis -d postgres \
		-c "SELECT 1 FROM pg_database WHERE datname='jarvis_test'" | grep -q 1 \
		|| $(COMPOSE) exec -T postgres psql -U jarvis -d postgres -c "CREATE DATABASE jarvis_test OWNER jarvis"
	uv run alembic upgrade head
	@echo "ready. run 'make api'"

up: ## Start Postgres
	$(COMPOSE) up -d

down: ## Stop Postgres (keeps the volume)
	$(COMPOSE) down

db-shell: ## psql into the dev database
	$(COMPOSE) exec postgres psql -U jarvis -d jarvis

migrate: ## Apply migrations
	uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add goals"
	uv run alembic revision --autogenerate -m "$(m)"

api: ## Run the API with reload
	JARVIS_BUILD_SHA=$$(git rev-parse --short HEAD) uv run uvicorn jarvis.main:app --reload

worker: ## Run the background worker: agent loop, connector polling, escalation, heartbeat
	uv run python -m jarvis.workers.main

scheduler: ## Run the schedule ticker (fires deadline rungs into the queue)
	uv run python -m jarvis.workers.scheduler

video-worker: ## Run the YouTube video worker (renders drafts, uploads after approval)
	uv run python -m jarvis.workers.video

stack: ## Run api + worker + scheduler in one terminal (Ctrl-C stops all)
	infra/scripts/stack.sh

tunnel: ## Public HTTPS URL for this Mac via Cloudflare — no account, no card, no port-forward
	infra/scripts/tunnel.sh

aws-launch: ## Create the AWS VM (t4g.small — free through Dec 2026) after `aws configure`
	infra/scripts/aws-launch.sh

deploy: ## One-command deploy to a VM: make deploy HOST=ubuntu@1.2.3.4 KEY=~/.ssh/key DOMAIN=x.duckdns.org
	infra/scripts/deploy.sh $(HOST) $(KEY) $(DOMAIN)

prod-up: ## Build the image and start everything behind Caddy (needs .env with JARVIS_DOMAIN)
	docker compose --env-file .env -f infra/compose/docker-compose.prod.yml up -d --build

prod-logs: ## Tail production logs
	docker compose --env-file .env -f infra/compose/docker-compose.prod.yml logs -f --tail=100

prod-down: ## Stop production (keeps volumes)
	docker compose --env-file .env -f infra/compose/docker-compose.prod.yml down

test: ## Run the test suite
	uv run pytest -q

test-live: ## Run accuracy evals against real providers (needs API keys)
	uv run pytest tests/evals -q --live-eval

seed: ## Seed a demo tenant (demo@jarvis-x.dev / demo-password-12345)
	uv run python scripts/seed_demo.py

demo-reset: ## Clear and re-seed the demo tenant only
	uv run python -m scripts.demo_reset

connect-google: ## Connect Gmail + Calendar (needs 'make api' running)
	uv run python -m scripts.connect_google

connectors: ## Show what is connected and how much it stores
	uv run python -m scripts.list_connectors

mobile: ## Run the Flutter app in Chrome against a local API
	cd apps/mobile && flutter run -d chrome --web-port 8081

mobile-test: ## Analyze and test the Flutter app
	cd apps/mobile && flutter analyze lib test && flutter test

openapi: ## Regenerate the OpenAPI document clients are built from
	JARVIS_BUILD_SHA=dev uv run python -c "import json; from jarvis.main import app; \
	  print(json.dumps(app.openapi(), indent=2))" > packages/contracts/openapi/jarvis.json

lint: ## Lint
	uv run ruff check apps/api tests

fmt: ## Autofix lint
	uv run ruff check apps/api tests --fix

clean: ## Stop Postgres and delete its volume
	$(COMPOSE) down -v
