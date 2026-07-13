# Testing

Shared unit-test rules live in [unittests.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/standards/unittests.md).

Shared build and target knowledge also applies:

- [knowledge/build-and-checks.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/build-and-checks.md)
- [knowledge/make-targets.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/make-targets.md)

This file documents only repository-specific testing behavior.

## Test Scope

The test scope covers extension-app startup (the app starts with no registered
routes) and the PostgreSQL-backed persistence layer described below.

## PostgreSQL-Backed Persistence Tests

The tests under [`../backend/tests/persistence/postgres/`](../backend/tests/persistence/postgres/) (and the shared `db` fixture in
[`../backend/tests/conftest.py`](../backend/tests/conftest.py)) run against a real PostgreSQL server. Each test creates a
throwaway database, applies the schema, and drops the database on teardown, so
tests stay isolated under random ordering.

- `make test` is the canonical way to run them: it executes pytest inside the
  compose backend container, which starts the healthy `postgres` service and
  reaches it at `postgres:5432`.
- Running bare `pytest` on the host fails these tests unless a PostgreSQL server
  is reachable. Point `MPT_TEST_DATABASE_URL` at one to override the default
  admin DSN (`postgresql://postgres:postgres@postgres:5432/usage_reporting`),
  for example `postgresql://postgres:postgres@localhost:5433/usage_reporting`
  for the compose service published on the host.

## Commands

Use the repository make targets:

```bash
make test
make check
make check-all
```

Repository command mapping:

- `make test` runs `pytest`
- `make check` runs `ruff format --check`, `ruff check`, `flake8`, `mypy`, and `uv lock --check`
- `make check-all` runs both checks and tests

The CI workflow in [`.github/workflows/pr-build-merge.yml`](../.github/workflows/pr-build-merge.yml) uses the same `make build` and `make check-all` flow.

## Pytest Configuration

Repository-specific test settings come from [`backend/pyproject.toml`](../backend/pyproject.toml):

- tests are discovered under `tests`
- `pythonpath` includes the repository root
- coverage is collected for `mpt_usage_reporting_extension`
- tests run with `--import-mode=importlib`

## Writing Tests

Repository-specific guidance:

- Use fixtures from [`tests/conftest.py`](../tests/conftest.py) where possible.
- Mock external Marketplace SDK calls rather than calling real services.
- Keep tests focused on the behavior of the extension layer, not on internals of `mpt-extension-sdk` itself.
- Follow the shared unit-test standard for AAA structure, parametrization, mocking rules, deterministic behavior, and coverage expectations.

## When Tests Are Required

Add or update tests when a change modifies:

- API request handling
- event processing
- pipeline step behavior
- command output
- dependency wiring in the extension app

If a change only affects documentation, tests are not required.
