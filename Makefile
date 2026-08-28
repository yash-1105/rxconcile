.DEFAULT_GOAL := help

VENV       := api/.venv
PY         := $(VENV)/bin/python
MYPY_CONF  := api/pyproject.toml

# The API port. 8000 is the default, but it may already be occupied on this
# machine by an unrelated service — override with `make dev API_PORT=8010`.
API_PORT   ?= 8000
WEB_PORT   ?= 5173

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

install: install-api install-web ## Install all dependencies

install-api: ## Create the Python venv and install the api package
	@if command -v uv >/dev/null 2>&1; then \
	  uv venv $(VENV) && VIRTUAL_ENV=$(VENV) uv pip install -e "api[dev]"; \
	else \
	  python3 -m venv $(VENV) && $(PY) -m pip install --upgrade pip && $(PY) -m pip install -e "api[dev]"; \
	fi

install-web: ## Install web dependencies
	cd web && npm install

# Kept as aliases so existing muscle memory still works.
setup: install
setup-api: install-api
setup-web: install-web

# --------------------------------------------------------------------------
# Development
# --------------------------------------------------------------------------

dev: ## Run the API and the web dev server together (API_PORT=8010 to move the API)
	@echo "API  http://localhost:$(API_PORT)"
	@echo "web  http://localhost:$(WEB_PORT)"
	@trap 'kill 0' EXIT INT TERM; \
	$(VENV)/bin/uvicorn rxconcile.main:app --reload --port $(API_PORT) & \
	(cd web && API_PORT=$(API_PORT) npm run dev) & \
	wait

api: ## Run only the API
	$(VENV)/bin/uvicorn rxconcile.main:app --reload --port $(API_PORT)

web: ## Run only the web dev server
	cd web && API_PORT=$(API_PORT) npm run dev

# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

test: ## Run the Python and web test suites
	$(PY) -m pytest api/tests -q
	cd web && npm test

typecheck: ## mypy (strict, verified) + tsc
	@./api/scripts/verify_mypy_strict.sh $(MYPY_CONF) $(PY)
	$(PY) -m mypy --config-file $(MYPY_CONF) api/rxconcile api/scripts api/tests
	cd web && npx tsc -b --noEmit

lint: ## ruff + oxlint
	$(PY) -m ruff check api
	cd web && npm run lint

build: ## Build the frontend
	cd web && npm run build

check: test typecheck lint build ## Everything CI would run

# --------------------------------------------------------------------------
# Vertex and samples
# --------------------------------------------------------------------------

warm: ## Pre-compute the extraction cache for the bundled samples so the demo is instant
	$(PY) api/scripts/warm_samples.py

samples: ## Regenerate the synthetic sample pairs
	$(PY) api/scripts/generate_samples.py

list-models: ## List Gemini models actually available to this project
	./api/scripts/list_models.sh

verify: ## Prove the Vertex chain works via curl (text + multimodal)
	./api/scripts/verify_vertex.sh

smoke: ## Prove the Vertex chain works via the google-genai SDK
	$(PY) api/scripts/smoke_gcp.py

.PHONY: help install install-api install-web setup setup-api setup-web \
        dev api web test typecheck lint build check samples list-models verify smoke
