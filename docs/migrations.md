# Migrations

Shared migration knowledge lives in:

- [knowledge/migrations.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/migrations.md)
- [knowledge/make-targets.md](https://github.com/softwareone-platform/mpt-extension-skills/blob/main/knowledge/make-targets.md)

This file documents repository-specific migration behavior only.

## When To Update This Document

Update this file when the repository changes:

- migration file locations
- migration command entry points
- required execution order
- rollout or safety constraints specific to this repository

## Full-Recalculate Data Migrations

Some data migrations rebuild the accumulation store by running a full-scope
`recalculate` (no date window, every configured product): the initial backfill
(`20260714111446_backfill_subscriptions_usage.py`) and the charge-period
re-bucketing for MPT-24709 (`20260828121703_recalculate_charge_period_buckets.py`).
The latter is mandatory when deploying the bucketing-rule change: historical
buckets were keyed by the statement's issued/cancelled month, while new writes
key by the charge's billing `period.end`, so without the rebuild old and new
buckets would not line up and the pushed estimates would mix both keyings.

Rollout constraints:

- A full recalculate streams every statement's charges and re-pushes every
  subscription's estimates — it is long-running and API-heavy. Run it once per
  environment, outside the daily run's schedule.
- The Kubernetes job template caps runs at `activeDeadlineSeconds: 600`; verify
  the migration run fits, or raise the limit for the migration job.
