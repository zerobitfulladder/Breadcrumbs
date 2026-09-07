# Breadcrumbs — build and run.
#
# Two ways to run it:
#
#   make dev    backend + Vite, hot reload, two ports. What you develop against.
#   make run    one process. The backend serves the built UI from its own port.
#
# `run` is the whole app on http://127.0.0.1:8000 with nothing else running —
# no Node, no second terminal. Node is still needed to *produce* the bundle,
# but not to serve it.

HOST ?= 127.0.0.1
PAGES ?= site
PORT ?= 8000

BACKEND  := backend
FRONTEND := frontend
DIST     := $(FRONTEND)/dist/index.html

# Rebuild when any of these change, and not otherwise.
FRONTEND_SRC := $(shell find $(FRONTEND)/src -type f 2>/dev/null) \
                $(FRONTEND)/index.html $(FRONTEND)/package.json \
                $(FRONTEND)/vite.config.ts

UV  := uv run --project $(BACKEND)
NPM := cd $(FRONTEND) &&

.DEFAULT_GOAL := help
.PHONY: help install dev dev-backend dev-frontend build run serve check backfill pages clean wipe-build

help: ## Show this help
	@echo "Breadcrumbs"
	@echo
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Library:  $${BREADCRUMBS_HOME:-~/Breadcrumbs}"
	@echo "  Override: make run PORT=9000 HOST=0.0.0.0"

install: ## Install backend and frontend dependencies
	$(UV) python -c "import backend" 2>/dev/null || uv sync --project $(BACKEND)
	$(NPM) npm ci

# --- development: two processes, hot reload on both ------------------------
dev: ## Backend and Vite together, with reload (ctrl-c stops both)
	@echo "backend  http://$(HOST):$(PORT)"
	@echo "frontend http://localhost:5173   <- open this one"
	@test -f $(PAGES)/index.html \
	  && echo "readonly http://localhost:5173/readonly/   (published copy; make pages to refresh)" \
	  || echo "readonly not built yet — run 'make pages'"
	@trap 'kill 0' INT TERM EXIT; \
	  $(MAKE) --no-print-directory dev-backend & \
	  $(MAKE) --no-print-directory dev-frontend & \
	  wait

dev-backend: ## Backend only, with reload
	$(UV) uvicorn backend.api:app --host $(HOST) --port $(PORT) \
	  --reload --reload-dir $(BACKEND)/src

dev-frontend: ## Vite dev server only
	$(NPM) npm run dev

# --- production: one process ------------------------------------------------
build: $(DIST) ## Build the frontend bundle

# VITE_API_BASE is set empty on purpose: the bundle then calls /api/... on
# whatever origin serves it, so the same build works on any host or port. The
# default is an absolute http://127.0.0.1:8000, which only suits the dev server.
$(DIST): $(FRONTEND_SRC)
	$(NPM) VITE_API_BASE= npm run build

run: build serve ## Build, then serve everything from the backend alone

serve: ## Serve the existing build from the backend alone (no rebuild)
	@test -f $(DIST) || { echo "No bundle yet — run 'make build'."; exit 1; }
	@echo "Breadcrumbs on http://$(HOST):$(PORT)"
	$(UV) uvicorn backend.api:app --host $(HOST) --port $(PORT)

# `tsc --noEmit` on the root tsconfig checks nothing: it is a references-only
# file with "files": [], so tsc has no inputs and exits 0 regardless. `-b`
# builds the referenced projects, which is what actually typechecks src.
# --- publishing: a read-only copy, no backend ------------------------------
# Freezes the library into static JSON and builds the reader against it, so the
# result is a plain directory of files that any static host will serve.
#
# What it leaves out is as important as what it includes. No PDFs: a paywalled
# article is the publisher's to distribute, not yours. No settings and no
# conversations: that table holds your API keys. Only the routes listed in
# export_static.ROUTES are written, so nothing is published by accident.
#
# VITE_STATIC=1 switches the client onto those files and refuses every write,
# and the base is relative so the result works from a project page
# (user.github.io/repo) as happily as from a domain root.
pages: ## Build a read-only copy of the library for static hosting
	rm -rf $(PAGES)
	$(UV) python -m backend.export_static --out $(PAGES)/data
	$(NPM) VITE_API_BASE= VITE_STATIC=1 VITE_STATIC_BASE=./data \
	  npx vite build --base ./ --outDir ../$(PAGES) --emptyOutDir false
	@touch $(PAGES)/.nojekyll
	@# Put back the marker this target's own rm -rf removed. The directory is
	@# tracked but its contents are not, and the backend's /readonly mount
	@# checks for it at start-up.
	@touch $(PAGES)/.gitkeep
	@echo
	@echo "  $(PAGES)/ is ready to publish — $$(du -sh $(PAGES) | cut -f1)"
	@echo "  Preview it with:  python3 -m http.server -d $(PAGES) 8080"

backfill: ## Re-fetch every paper's full reference list, then relink
	$(UV) python -m backend.backfill

check: ## Typecheck the frontend and import the backend
	$(NPM) npx tsc -b --force
	$(UV) python -c "from backend.api import app; print('backend imports clean')"

clean: ## Remove the bundle and Python caches
	rm -rf $(FRONTEND)/dist
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +

wipe-build: clean ## Also remove installed dependencies
	rm -rf $(FRONTEND)/node_modules $(BACKEND)/.venv
