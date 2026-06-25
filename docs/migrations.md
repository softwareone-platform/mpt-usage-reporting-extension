# Migrations

Use this document only for migration details that are specific to the repository.

Shared migration knowledge lives in:

- [knowledge/migrations.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/migrations.md)
- [knowledge/make-targets.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/make-targets.md)

If the repository does not yet have repository-specific migration rules, keep this file short and rely on the shared migration knowledge above.

## Repository-Specific Details

### Persistent SQLite store

Schema migrations in this repository create and evolve a local SQLite database used
by the accumulation stage.

- The database file defaults to `storage.db` in the backend root. Override the
  location with the `MPT_BSU_DB_PATH` environment variable.
- The file and the migration state file (`.migrations-state.json`) are gitignored;
  they are recreated by running `make migrate-schema`.
- Connection, schema creation, and decimal handling live in
  `mpt_usage_reporting_extension.persistence.sqlite.database`. Monetary columns are stored
  as `DECIMAL` (TEXT) and round-tripped through `decimal.Decimal` so precision is
  preserved with no float drift.
- `20260603155923_create_accumulation_tables.py` creates
  `subscription_monthly_accumulation` and `agreement_monthly_accumulation`.

## What To Add Here

Add repository-specific migration details only when they exist, for example:

- where migration files live
- which migration commands are actually used in this repository
- required execution order or rollout rules
- operational constraints or safety checks
- differences from the shared migration knowledge

## Documentation Rule

When repository-specific migration behavior is introduced or changed, update this document in the same change.
