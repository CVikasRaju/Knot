# Knot - developer entry points. `make help` lists everything.
.DEFAULT_GOAL := help
PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: help venv install graph seed test api mcp ui ui-install ui-build demo demo-future demo-incident demo-archaeology clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	python3 -m venv .venv

install: ## Install runtime + dev dependencies
	$(PIP) install -r requirements-dev.txt

graph: ## Start FalkorDB (+ browser) via docker compose
	docker compose up -d falkordb

seed: ## Seed the demo enterprise into FalkorDB
	$(PY) scripts/seed_knot.py --reset

test: ## Run the test suite against a live FalkorDB
	$(PY) -m pytest

api: ## Start the REST API on :8000
	$(PY) -m uvicorn src.api.main:app --reload --port 8000

mcp: ## Start the MCP server on stdio
	$(PY) -m src.mcp_server.server

ui-install: ## Install frontend dependencies
	cd frontend && npm install

ui: ## Start the 3D command center on :5173 (needs `make api` running)
	cd frontend && npm run dev

ui-build: ## Production build of the command center
	cd frontend && npm run build

demo: demo-future demo-incident demo-archaeology ## Run all three demo scenarios

demo-future: ## Counterfactual PR-812 simulation
	$(PY) scripts/run_scenario.py --mode future_sim --pr_id PR-812 --breaking --latency-delta 180

demo-incident: ## Live ALT-904 outage triage
	$(PY) scripts/run_scenario.py --mode live_incident --alert_id ALT-904

demo-archaeology: ## Decision provenance for a "why does this exist?" question
	$(PY) scripts/run_scenario.py --mode archaeology \
		--query "Why does PaymentService bypass Redis on retry?"

clean: ## Remove caches
	rm -rf .pytest_cache **/__pycache__ src/**/__pycache__ tests/__pycache__
