# Architecture

This document describes the structure, major components, boundaries, and layer
responsibilities of `mpt-usage-reporting-extension`. For configuration, local
setup, external systems, testing, and migrations, see the documents linked
below.

## Purpose

`mpt-usage-reporting-extension` is a SoftwareOne Marketplace Platform (MPT)
extension that reports billing subscription usage. It has two faces:

- a **CLI batch job** that selects billing statements, streams their charges,
  accumulates them per subscription/agreement and month, and persists the
  totals to a local SQLite store;
- a small **extension app** (event, API, and plug routes) plus a TypeScript
  frontend that surfaces agreement actions in the Marketplace UI.

The backend is built on the MPT Extension SDK.

## CLI

`backend/mpt_usage_reporting_extension/cli.py` is a Typer CLI exposed as the
`mpt-billing-subscription-usage` console script (`pyproject.toml`
`[project.scripts]` -> `cli:main`). Commands:

- `run` — the main command. Resolves a date window (`--date`, or `--from-date`
  / `--till-date`; defaults to yesterday UTC), then collects, accumulates, and
  persists usage for that window.
- `billing_subscription_usage` — placeholder command (not yet implemented).

### Run data flow

1. Resolve the window (`window.py` -> `RunWindow`, a half-open `[start, end)` UTC range).
2. Build a `RunContext` (`context.py`) with the MPT API service, window, and product ids.
3. `StatementSelector` (`statements.py`) selects billing statements via RQL.
4. `ChargeStreamer` (`charges.py`) streams charges line-by-line (JSONL).
5. `ChargeAccumulator` (`accumulation.py`) groups charges by
   `AccumulationKey` = `(agreement_id, subscription_id, year, month)` into `ChargeTotals`.
6. `ChargePersister` (`charge_persistence.py`) upserts the totals into SQLite.

## Persistence (SQLite)

`backend/mpt_usage_reporting_extension/persistence/sqlite/` holds the store:

- `database.py` — `SqliteDatabase` context manager (Decimal handling, busy timeout).
- `repositories.py` — subscription and agreement accumulation repositories with additive upserts.
- Tables (created by `backend/migrations/`): `subscription_monthly_accumulation`
  (PK `subscription_id, year, month, agreement_id`) and
  `agreement_monthly_accumulation` (PK `agreement_id, year, month`); both store `ppx1`, `spx1`, `updated_at`.

The DB file defaults to the `DEFAULT_DB_PATH` constant (`storage.db` in the
backend root) and can be overridden with the `MPT_DB_PATH` environment variable.
See [migrations.md](migrations.md).

## Extension app

`backend/mpt_usage_reporting_extension/app.py` registers:

- **event route** `POST /events/v2/orders/purchase` — runs the purchase pipeline
  (`flows/pipelines/purchase.py`, `flows/steps/log_order.py`);
- **API routes** `GET /api/v2/agreements/{id}` and `POST /api/v2/agreements/{id}/sync`;
- **plug routes** — register the agreement UI plugs.

## Frontend

`frontend/` is a TypeScript/React plug UI (esbuild) providing the agreement
"Sync account" tab, line actions, and the agreement actions wizard. It builds to
`backend/static/` and is served through the backend's plug routes.

## Components

| Module | Responsibility |
|---|---|
| `cli.py` | Typer CLI entry (`run`, `billing_subscription_usage`) |
| `statements.py`, `charges.py` | Statement selection and charge streaming from the MPT API |
| `accumulation.py`, `context.py`, `window.py` | Accumulation keys/totals, run context, and the date window |
| `charge_persistence.py`, `persistence/` | Persisting accumulated totals to SQLite |
| `app.py`, `routers/` | Extension event/API/plug routes |
| `flows/` | Order pipelines and steps |
| `mpt_client.py`, `settings.py` | MPT API service and runtime settings |

## External integrations

MPT Marketplace API (billing statements/charges, agreements), the MPT Extension
SDK, optional Airtable (`mpt-tool` storage), and Jaeger/OpenTelemetry for local
tracing. See [external-integrations.md](external-integrations.md).

## Deployment shape

The container image is built from the multi-stage `Dockerfile` and started via
`mpt-ext run`. `compose.yaml` runs the backend, frontend watcher, and Jaeger;
`compose.local.yaml` overrides it for `--local` mode. See
[deployment.md](deployment.md) and [local-development.md](local-development.md).

## Related documentation

- [local-development.md](local-development.md) — local setup and run
- [contributing.md](contributing.md) — development workflow and commands
- [testing.md](testing.md) — test strategy and execution
- [deployment.md](deployment.md) — configuration and deployment model
- [external-integrations.md](external-integrations.md) — external systems
- [migrations.md](migrations.md) — migration workflow and the SQLite store
