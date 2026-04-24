.PHONY: install sync test cov lint typecheck fmt smoke schedule clean package help

SHELL := /usr/bin/env bash
UV    ?= uv

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort | awk 'BEGIN{FS=":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

install: ## Bootstrap uv + create venv + install dev deps + smoke test
	@bin/install.sh

sync: ## Recreate the venv from uv.lock (includes dev + viz extras)
	$(UV) sync --frozen --extra dev --extra viz

test: ## Run the test suite
	$(UV) run -- pytest -q

cov: ## Run tests with coverage
	$(UV) run -- pytest --cov=solver --cov-report=term-missing

lint: ## Ruff lint
	$(UV) run -- ruff check solver tests

typecheck: ## Mypy typecheck (non-blocking)
	-$(UV) run -- mypy solver

fmt: ## Autofix lint issues
	$(UV) run -- ruff check --fix solver tests

smoke: ## End-to-end pipeline against the docs example
	@set -e; \
	tmp=$$(mktemp -d); \
	$(UV) run -- python -m solver.parse_tasks docs/example-tasks.md docs/example-config.yml > $$tmp/in.json; \
	$(UV) run -- python -m solver.scheduler < $$tmp/in.json > $$tmp/out.json; \
	$(UV) run -- python -m solver.render_schedule $$tmp/out.json example > $$tmp/schedule.md; \
	echo "smoke OK ($$(wc -l <$$tmp/schedule.md) lines)"; \
	rm -rf $$tmp

schedule: ## Rebuild docs/example-schedule.md + docs/images/* from current code
	@set -e; \
	tmp=$$(mktemp -d); \
	$(UV) run -- python -m solver.parse_tasks docs/example-tasks.md docs/example-config.yml > $$tmp/in.json; \
	$(UV) run -- python -m solver.scheduler < $$tmp/in.json > $$tmp/out.json; \
	mkdir -p docs/images; \
	$(UV) run --extra viz -- python -m solver.visualize $$tmp/out.json docs/images --feature example >/dev/null; \
	$(UV) run -- python -m solver.render_schedule $$tmp/out.json "Create Taskify" --image-prefix images/example > docs/example-schedule.md; \
	echo "regenerated docs/example-schedule.md + docs/images/example-{dag,gantt}.png"; \
	rm -rf $$tmp

package: ## Produce a distributable zip of the extension
	@rm -rf dist && mkdir -p dist
	@git rev-parse --is-inside-work-tree >/dev/null 2>&1 && \
	  git archive --format=zip -o dist/spec-kit-schedule.zip HEAD || \
	  (cd .. && zip -r "$(PWD)/dist/spec-kit-schedule.zip" \
	     "$$(basename $(PWD))" -x '*/.git/*' '*/.venv/*' '*/__pycache__/*' \
	     '*/.ruff_cache/*' '*/.pytest_cache/*' '*/.mypy_cache/*' '*/dist/*')
	@echo "dist/spec-kit-schedule.zip ready"

clean: ## Remove caches and build outputs
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist .venv
