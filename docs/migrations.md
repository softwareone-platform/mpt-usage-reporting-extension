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

## Scoped Recalculate Data Migrations

`20260922090000_recalculate_split_currency_agreements.py` (MPT-25312) is
mandatory when deploying the `BSPx1` change: historical `spx1` buckets summed
`SPx1` (the billing-currency amount) while new writes sum `BSPx1` (the
authorization-currency amount). The two fields differ only where an agreement's
`price.billingCurrency` differs from its `price.currency` (for example "AWS
Vietnam USD": priced in USD, billed in VND); everywhere else `rate` is 1 and the
stored buckets are already correct. The split is read from the agreements'
current currency fields, so an agreement whose currencies differed when its
buckets were written but match now is not detected. The migration therefore
lists the configured products' agreements through the commerce API, keeps those
with a currency split (`split_currency_agreement_ids` in
`services/estimate_currency.py`), and runs an
agreement-scoped `recalculate` for each: its buckets are deleted, re-filled from
its statements only, and its estimates re-pushed. The listing is read to the end
before the first recalculation, so paging never interleaves with the rebuilds.
Each agreement is one tracked execution with its own Teams card; a failing
agreement is logged and the others still run. If the listing itself fails
part-way, the agreements it already returned are still recalculated before the
listing error is raised. An agreement with a charge that cannot be summed (no
`PPx1`, or no `BSPx1` and no `SPx1` in its purchase currency) is not rebuilt: its
buckets and estimates are left as they were, and it counts as failed because its
execution completes with errors. The migration fails
at the end listing the failed ids so it can be re-run (an agreement-scoped
recalculate is idempotent). With no split-currency agreements it only logs and
finishes.

Rollout constraints:

- A full recalculate streams every statement's charges and re-pushes every
  subscription's estimates — it is long-running and API-heavy. Run it once per
  environment, outside the daily run's schedule.
- The Kubernetes job template caps runs at `activeDeadlineSeconds: 600`; verify
  the migration run fits, or raise the limit for the migration job.
