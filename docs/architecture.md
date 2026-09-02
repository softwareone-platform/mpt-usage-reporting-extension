# Architecture

This document describes the structure, major components, boundaries, and layer
responsibilities of `mpt-usage-reporting-extension`. For configuration, local
setup, external systems, testing, and migrations, see the documents linked
below.

## Purpose

`mpt-usage-reporting-extension` is a SoftwareOne Marketplace Platform (MPT)
extension that reports billing subscription usage. Its product is a **CLI batch
job** that selects billing statements, streams their charges, accumulates them
per subscription/agreement and month, persists the totals to a PostgreSQL
store, and pushes the resulting price estimates back to each subscription. A
bare **extension app** (`app.py`) is kept only so `mpt-ext run` can serve the
SDK's built-in endpoints; it registers no routes of its own.

The backend is built on the MPT Extension SDK.

## CLI

`backend/mpt_usage_reporting_extension/cli/` is a Typer CLI package (one module per command
under `cli/commands/`, assembled in `cli/_app.py` and re-exported from `cli/__init__.py`) exposed
as the `mpt-billing-subscription-usage` console script (`pyproject.toml` `[project.scripts]` ->
`cli:main`). Commands:

- `run` — the main command. Resolves a date window (`--date`, or `--from-date`
  / `--till-date`; defaults to yesterday UTC), then collects, accumulates, and
  persists usage for that window, and pushes the resulting price estimates back
  to each subscription.
- `cleanup` — prunes both accumulation tables to the rolling 18-month retention
  window ending at the given month (`--date`; defaults to the current UTC month).
- `push-estimates by-id` / `push-estimates by-updated-at` — recompute and push estimates
  from already-stored usage (no statement download), selected by id or by write date.
- `delete` — delete **all** stored accumulation buckets for one scope (exactly one of
  `--product-id` / `--agreement-id` / `--subscription-id` / `--seller-id`).
  `--product-id`/`--seller-id` are resolved to their agreements via the commerce API and clear both
  tables by `agreement_id`; `--subscription-id` clears only that subscription's buckets (the shared
  agreement bucket aggregates its siblings). See `services/bucket_delete.py` (`BucketDeleter`).
- `recalculate` — rebuild a scope idempotently: `delete` the scope's buckets, then run the normal
  fill (select -> accumulate -> persist -> push estimates) so re-runs do not double-count. Optional
  `--product-id` / `--seller-id` (none = all configured products); requires `--from-date` and
  `--till-date` to bound statement selection. `pipeline.recalculate` reuses every `run` stage and
  adds only the reset step.
  `--dry-run` previews execution and exits before any delete/persist/push/cleanup mutation.

### Run data flow

`cli.run` resolves the inputs (steps 1–2), then hands a `RunContext` to
`UsageReportingPipeline` (`pipeline.py`), which runs the remaining stages inside a
single `PostgresDatabase` context — opened first, so a database failure aborts before
any API work. Each stage is a service constructed with only the dependencies it needs
(the API service and/or the repositories); `RunContext` carries the run inputs.

1. Resolve the window (`window.py` -> `RunWindow`, a half-open `[start, end)` UTC range).
2. Build a `RunContext` (`context.py`) with the MPT API service, window, and product ids.
3. `StatementSelector` (`services/statements.py`) selects billing statements via RQL.
4. `ChargeStreamer` (`services/charges.py`) streams charges line-by-line (JSONL).
5. `ChargeAccumulator` (`accumulation.py`) groups charges by
   `AccumulationKey` = `(agreement_id, subscription_id, year, month)` into `ChargeTotals` —
   the month comes from the charge's billing `period.end`, falling back to the statement's
   cancelled/issued date when the charge has no usable period. The platform returns an absent
   date as the `0001-01-01T00:00:00.000Z` sentinel rather than as `null`, so a date outside the
   storable year range counts as absent and the next candidate is used. Deploying a
   bucketing-rule change requires a full-scope historical recalculate so persisted buckets and
   pushed estimates are regrouped under the new keying — see
   [migrations.md](migrations.md#full-recalculate-data-migrations).
6. `AccumulationPersister` (`services/charge_persistence.py`) upserts the totals into PostgreSQL.
   A bucket with no storable billing month is skipped and counted, never written, so one
   undatable charge cannot abort the run on the tables' year `CHECK` constraint.
7. `EstimatesUploader` (`services/estimates_uploader.py`) computes each real subscription's
   estimate from PostgreSQL — anchor-month `PPxM`/`SPxM` and trailing-12-month
   `PPxY`/`SPxY` sums, anchored on the previous (latest completed) calendar month. When the
   anchor month has no buckets — the normal state while that month's billing is still being
   generated — the monthly pair carries forward from the most recent earlier month in the window
   that has data, logged per subscription, so an estimate is never wiped just because the anchor
   month has not been billed yet. A figure with no backing buckets anywhere in the window is
   `null` (never a fabricated 0, which would overwrite a real estimate). Present sums are
   clamped to 0 because credits can push a total negative and MPT rejects negative prices — and
   concurrently `PUT`s `{"price": {SPxM, SPxY}}` back to the subscription via the MPT API —
   only the sales prices are sent; the platform recalculates the purchase prices — skipping
   synthetic (`agreement_additional_*`) and dateless buckets. It logs a
   per-subscription report (values + `OK`/`FAILED`) and exits non-zero on
   any failure. Charge streaming logs `[k/N]` progress per statement.

## Persistence (PostgreSQL)

`backend/mpt_usage_reporting_extension/persistence/postgres/` holds the store,
implementing the shared interfaces in `persistence/protocols.py` (including the
`Database` protocol the pipeline is annotated against):

- `database.py` — `PostgresDatabase` async context manager (one autocommit
  `psycopg` connection per run, dict rows), `resolve_database_url()` (reads
  `MPT_DATABASE_URL`, fails fast when unset), and `connect_sync()` for migrations.
- `repositories/` — one module per repository: `engine.py` (the shared additive-upsert
  engine: `INSERT ... ON CONFLICT ... DO UPDATE SET ppx1 = ppx1 + EXCLUDED.ppx1`),
  `subscription.py`, and `agreement.py`. Amounts are `NUMERIC`, so accumulation is
  exact decimal arithmetic; timestamps are `TIMESTAMPTZ` written as aware UTC datetimes.
- `insights.py` — command-execution and per-statement processing repositories
  (`INSERT ... RETURNING id`).
- Tables (created by `backend/migrations/`): `subscription_monthly_accumulation`
  (PK `subscription_id, year, month, agreement_id`) and
  `agreement_monthly_accumulation` (PK `agreement_id, year, month`); both store `ppx1`, `spx1`, `updated_at`.

The connection string comes from the `MPT_DATABASE_URL` environment variable.
See [migrations.md](migrations.md).

## Extension app

`backend/mpt_usage_reporting_extension/app.py` instantiates a bare
`ExtensionApp` with no event, API, or plug routes. It exists so `mpt-ext run`
can serve the SDK's built-in endpoints.

## Components

| Module | Responsibility |
|---|---|
| `cli/` | Typer CLI package — one module per command (`run`, `cleanup`, `delete`, `recalculate`, `push-estimates`) |
| `pipeline.py` | `UsageReportingPipeline` — orchestrates the end-to-end `run` and `recalculate` |
| `selectors.py` | `--product-id`/`--agreement-id`/`--subscription-id`/`--seller-id` selectors, shared by `delete`, `recalculate`, and `push-estimates` |
| `services/statements.py`, `services/charges.py` | Statement selection and charge streaming from the MPT API |
| `accumulation.py`, `context.py`, `window.py` | Accumulation keys/totals, run context, and the date window |
| `services/charge_persistence.py`, `services/bucket_delete.py`, `persistence/` | Persisting accumulated totals to PostgreSQL and deleting buckets by scope/month range |
| `services/estimates_uploader.py` | `EstimatesUploader` — push `SPxM`/`SPxY` estimates to subscriptions (purchase prices are recalculated by the platform; figures without backing buckets are sent as `null`, and negative totals are clamped to 0 because MPT rejects negative prices), with a per-run report |
| `services/execution_notifier.py` | `ExecutionNotifier` — report each `run`/`recalculate` execution to MS Teams (success with the run report, or failure with error and stacktrace); disabled when `MPT_MSTEAMS_WEBHOOK_URL` is unset |
| `steps.py` | `logged_step` — logs each pipeline stage's start, end, and duration alongside its `@trace_span` |
| `observability.py` | Bootstraps the SDK's logging and tracing for CLI runs, which happen outside the SDK serve runtime that would otherwise do it |
| `app.py` | Bare `ExtensionApp` served by `mpt-ext run` |
| `mpt_client.py`, `settings.py` | MPT API service and runtime settings |

## External integrations

MPT Marketplace API (billing statements/charges, subscription price estimates,
agreements), the MPT Extension SDK, MS Teams execution notifications via a
Workflows webhook (`mpt-extension-contrib-custom-notifications`), optional
Airtable (`mpt-tool` storage), and Jaeger/OpenTelemetry for local tracing. See
[external-integrations.md](external-integrations.md).

## Deployment shape

The container image is built from the multi-stage `Dockerfile` and started via
`mpt-ext run`. `compose.yaml` runs the backend and Jaeger;
`compose.local.yaml` overrides it for `--local` mode. See
[deployment.md](deployment.md) and [local-development.md](local-development.md).

## Related documentation

- [local-development.md](local-development.md) — local setup and run
- [contributing.md](contributing.md) — development workflow and commands
- [testing.md](testing.md) — test strategy and execution
- [deployment.md](deployment.md) — configuration and deployment model
- [external-integrations.md](external-integrations.md) — external systems
- [migrations.md](migrations.md) — migration workflow and the database schema
