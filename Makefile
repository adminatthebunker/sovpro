# ═══════════════════════════════════════════════════════════════════════════
# SovereignWatch — operator Makefile
# ═══════════════════════════════════════════════════════════════════════════
.DEFAULT_GOAL := help
.PHONY: help env check up down build logs ps restart \
        ingest-mps ingest-mlas ingest-councils seed scan scan-full \
        stats refresh db-psql db-reset \
        fmt lint typecheck test \
        geoip-download \
        cli

COMPOSE := docker compose

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "}; /^[a-zA-Z0-9_-]+:.*?## / \
	  {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create .env from template if missing
	@test -f .env || cp .env.example .env && echo ".env created"
	@echo "Edit .env and set DB_PASSWORD + WEBHOOK_SECRET"

check: ## Verify dependencies are present
	@command -v docker >/dev/null || (echo "docker missing" && exit 1)
	@$(COMPOSE) version >/dev/null || (echo "docker compose plugin missing" && exit 1)
	@test -f .env || (echo "Missing .env — run 'make env' first" && exit 1)
	@echo "OK"

up: check ## Build and start the stack
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

build: ## Rebuild images
	$(COMPOSE) build --pull

logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=200

ps: ## Show service status
	$(COMPOSE) ps

restart: ## Restart services
	$(COMPOSE) restart

# ── Data operations ────────────────────────────────────────────────
ingest-mps: ## Fetch federal MPs from Open North
	$(COMPOSE) run --rm scanner ingest-mps

ingest-mlas: ## Fetch Alberta MLAs from Open North
	$(COMPOSE) run --rm scanner ingest-mlas

ingest-councils: ## Fetch Edmonton + Calgary councils
	$(COMPOSE) run --rm scanner ingest-councils

seed: ## Seed referendum organizations
	$(COMPOSE) run --rm scanner seed-orgs

scan: ## Scan stale websites
	$(COMPOSE) run --rm scanner scan

scan-full: ## Scan ALL websites (ignore stale check)
	$(COMPOSE) run --rm scanner scan --stale-hours 0

stats: ## Print sovereignty stats
	$(COMPOSE) run --rm scanner stats

refresh: ## Refresh materialized map views
	$(COMPOSE) run --rm scanner refresh-views

# ── Database helpers ────────────────────────────────────────────────
db-psql: ## Open psql shell
	$(COMPOSE) exec db psql -U sw -d sovereignwatch

db-reset: ## DESTRUCTIVE: drop pgdata volume and recreate
	@echo "This will destroy all scan data. Type 'yes' to continue: "; \
	read ans; \
	if [ "$$ans" = "yes" ]; then \
	  $(COMPOSE) down; \
	  docker volume rm sovpro_pgdata || true; \
	  $(COMPOSE) up -d db; \
	  echo "Reset complete"; \
	else echo "Aborted"; fi

# ── Dev / CI ────────────────────────────────────────────────────────
typecheck-api: ## Typecheck the API
	cd services/api && npm run typecheck

typecheck-frontend: ## Typecheck the frontend
	cd services/frontend && npm run typecheck

typecheck: typecheck-api typecheck-frontend ## Typecheck all TS services

# ── External assets ────────────────────────────────────────────────
geoip-download: ## Reminder about MaxMind download
	@echo "GeoLite2 databases are licensed. Create a free MaxMind account at:"
	@echo "  https://www.maxmind.com/en/geolite2/signup"
	@echo "Then place these files in ./data/ :"
	@echo "  - GeoLite2-City.mmdb"
	@echo "  - GeoLite2-ASN.mmdb"

# ── CLI ────────────────────────────────────────────────────────────
cli: ## Run sovpro CLI
	@./cli/sovpro $(ARGS)
