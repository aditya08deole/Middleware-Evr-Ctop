# Single entry point for building, running and checking the stack.
#
# Every target that starts containers runs scripts/preflight.sh first, so a
# missing encryption.key is caught before Docker can turn it into a
# directory and orphan every stored credential.
#
# Production uses docker-compose.yml alone. Development must explicitly add
# docker-compose.dev.yml — see the `dev` target.

SHELL := /bin/bash
COMPOSE := docker compose
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml
HEALTH_URL := http://localhost:8090/health
HEALTH_TIMEOUT := 120

.DEFAULT_GOAL := help
.PHONY: help preflight build up down restart dev logs status health test lint fmt fmt-check audit shell ps \
        deploy rollback rollback-list backup watchdog bootstrap

help: ## Show this help
	@echo "CTOP middleware — available targets:"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Deploy:  make deploy   Revert:  make rollback   Dev:  make dev"

# --- Phase 5: deploy and revert -------------------------------------------
deploy: ## Build, start and verify health; auto-rolls back if unhealthy
	@bash scripts/deploy.sh

rollback: ## Revert to the previously deployed image
	@bash scripts/rollback.sh

rollback-list: ## Show image tags available to roll back to
	@bash scripts/rollback.sh --list

# --- Phase 4: host operations ---------------------------------------------
bootstrap: ## One-time Ubuntu server setup (requires sudo)
	sudo bash scripts/bootstrap-ubuntu.sh

backup: ## Back up encryption.key, the Firebase key and the SQLite mirror
	@bash scripts/backup.sh

watchdog: ## Check now whether deliveries to CTOP are failing
	@bash scripts/watchdog.sh

preflight: ## Verify secrets, permissions and Docker before starting
	@bash scripts/preflight.sh

build: preflight ## Build the production image
	$(COMPOSE) build

up: preflight ## Start the production stack, then wait for health
	$(COMPOSE) up -d --remove-orphans
	@$(MAKE) --no-print-directory health

down: ## Stop and remove the stack
	$(COMPOSE) down

restart: ## Restart the middleware container only
	$(COMPOSE) restart middleware
	@$(MAKE) --no-print-directory health

dev: preflight ## Start with the development overlay (live code mount, debug logs)
	$(COMPOSE_DEV) up --build

logs: ## Follow middleware logs
	$(COMPOSE) logs -f --tail=100 middleware

ps: ## Show container status
	$(COMPOSE) ps

status: ## Container status plus a health probe
	@$(COMPOSE) ps
	@echo
	@curl -fsS $(HEALTH_URL) && echo || echo "health endpoint not responding"

health: ## Block until /health responds, or fail after HEALTH_TIMEOUT seconds
	@echo "Waiting up to $(HEALTH_TIMEOUT)s for $(HEALTH_URL) ..."
	@deadline=$$(( $$(date +%s) + $(HEALTH_TIMEOUT) )); \
	until curl -fsS $(HEALTH_URL) >/dev/null 2>&1; do \
		if [ $$(date +%s) -ge $$deadline ]; then \
			echo "FAILED: not healthy after $(HEALTH_TIMEOUT)s. Recent logs:" >&2; \
			$(COMPOSE) logs --tail=40 middleware >&2; \
			exit 1; \
		fi; \
		sleep 3; \
	done; \
	echo "Healthy."

# --- Local quality gates: identical commands to .github/workflows/ci.yml, so
# --- "it passed locally" and "it passed in CI" mean the same thing.
test: ## Run the test suite (skips the slow stress test)
	# `python -m pytest`, never bare `pytest`: the console script does not
	# put the repo root on sys.path, so tests/conftest.py's
	# `from models import db` fails with ModuleNotFoundError.
	python -m pytest

lint: ## Lint with ruff
	ruff check .

fmt: ## Format with black
	black .

fmt-check: ## Check formatting without rewriting
	black --check .

audit: ## Report known vulnerabilities in pinned dependencies
	pip-audit -r requirements.txt

shell: ## Open a shell inside the running middleware container
	$(COMPOSE) exec middleware /bin/bash
