# Migrations

Use this document only for migration details that are specific to the repository.

Shared migration knowledge lives in:

- [knowledge/migrations.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/migrations.md)
- [knowledge/make-targets.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/make-targets.md)

If the repository does not yet have repository-specific migration rules, keep this file short and rely on the shared migration knowledge above.

## Repository-Specific Details

### PostgreSQL store

Schema migrations in this repository create and evolve the PostgreSQL database used
by the accumulation stage. They live in `backend/migrations/` and run through
`make migrate-schema`. The migration state file (`.migrations-state.json`) is
gitignored; it is recreated by running `make migrate-schema`.

- Connections resolve the URL from the `MPT_DATABASE_URL` environment variable via
  `mpt_usage_reporting_extension.persistence.postgres.database.connect_sync()`. When the
  variable is unset the migration fails fast instead of silently marking the
  environment as migrated, so `MPT_DATABASE_URL` must be set wherever
  `mpt-service-cli migrate --schema` runs.
- Monetary columns use native `NUMERIC` (psycopg round-trips `decimal.Decimal`
  losslessly), timestamps use `TIMESTAMPTZ`, and surrogate keys use
  `BIGINT GENERATED ALWAYS AS IDENTITY` (inserts must use `RETURNING id`).
- `20260713120000_create_postgres_accumulation_tables.py` creates
  `subscription_monthly_accumulation` and `agreement_monthly_accumulation`;
  `20260713120100_create_postgres_insights_tables.py` creates `command_execution`
  and `statement_processing`.
- The DDL is idempotent (`IF NOT EXISTS`), so re-running a migration against an
  environment with lost or missing state is safe.

## What To Add Here

Add repository-specific migration details only when they exist, for example:

- where migration files live
- which migration commands are actually used in this repository
- required execution order or rollout rules
- operational constraints or safety checks
- differences from the shared migration knowledge

## Documentation Rule

When repository-specific migration behavior is introduced or changed, update this document in the same change.
