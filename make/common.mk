DC ?= docker compose -f compose.yaml
RUN = $(DC) run --rm backend
RUN_IT = $(DC) run --rm -it backend

bash:  ## Open a bash shell
	$(RUN_IT) bash

build:  ## Build backend image
	$(DC) build backend
	$(RUN) uv sync

check:  ## Check code quality
	$(RUN) bash -c "uv run ruff format --check . && uv run ruff check . && uv run flake8 . && uv run mypy . && uv lock --check"

check-all:  ## Run checks, tests, and metadata validation
	$(MAKE) check
	$(MAKE) test
	$(RUN) bash -c 'trap "rm -f meta.yaml meta.generated.yaml" EXIT; while IFS= read -r line || [ -n "$$line" ]; do case "$$line" in ""|\#*) continue;; esac; export "$$line"; done < .env.sample; uv run mpt-ext meta generate && uv run mpt-ext meta validate'

down:  ## Stop and remove containers
	$(DC) down

format:  ## Format code
	$(RUN) bash -c "uv run ruff check --select I --fix . && uv run ruff format ."

logs: ## Show logs
	$(DC) logs -f backend

run:  ## Run service in platform integration mode
	$(DC) up backend

run-local:  ## Run backend in --local mode with Jaeger
	$(DC) -f compose.local.yaml up backend

test:  ## Run tests
	$(RUN) pytest $(args)

uv-add: ## Add a production dependency (pkg=<package_name>)
	$(call require,pkg)
	$(RUN) bash -c "uv add $(pkg)"
	$(MAKE) build

uv-add-dev: ## Add a dev dependency (pkg=<package_name>)
	$(call require,pkg)
	$(RUN) bash -c "uv add --dev $(pkg)"
	$(MAKE) build

uv-upgrade: ## Upgrade all packages or a specific package (use pkg="package_name" to target one)
	$(RUN) bash -c "uv lock $(if $(pkg),--upgrade-package $(pkg),--upgrade) && uv sync"
	$(MAKE) build
