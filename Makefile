.DEFAULT_GOAL := help
VENV := api/.venv
PY := $(VENV)/bin/python

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: setup-api setup-web ## Install all dependencies

setup-api: ## Create the Python venv and install the api package
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "api[dev]"

setup-web: ## Install web dependencies
	cd web && npm install

test: ## Run Python tests
	$(PY) -m pytest api/tests -q

typecheck: ## mypy (strict) + tsc
	$(PY) -m mypy api/rxconcile
	cd web && npx tsc -b --noEmit

lint: ## ruff + oxlint
	$(PY) -m ruff check api
	cd web && npm run lint

dev: ## Run the web dev server
	cd web && npm run dev

list-models: ## List Gemini models actually available to this project
	./api/scripts/list_models.sh

verify: ## Prove the Vertex chain works (text + multimodal)
	./api/scripts/verify_vertex.sh

.PHONY: help setup setup-api setup-web test typecheck lint dev list-models verify
