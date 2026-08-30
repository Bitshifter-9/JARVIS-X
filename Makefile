# JARVIS X — common tasks. Run `make help` for the list.
.DEFAULT_GOAL := help
COMPOSE := docker compose -f infra/compose/docker-compose.dev.yml

.PHONY: help bootstrap up down db-shell migrate revision api test test-live lint fmt clean

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

test: ## Run the test suite
	uv run pytest -q

test-live: ## Run accuracy evals against real providers (needs API keys)
	uv run pytest tests/evals -q --live-eval

lint: ## Lint
	uv run ruff check apps/api tests

fmt: ## Autofix lint
	uv run ruff check apps/api tests --fix

clean: ## Stop Postgres and delete its volume
	$(COMPOSE) down -v
