# Error Handling

This document describes how this repository raises, translates, records, and
reports errors.

## Exception hierarchy

All package exceptions live in
`backend/mpt_usage_reporting_extension/exceptions.py` and are rooted in a
single base:

```text
ExtensionError
├── ConfigurationError            missing or invalid configuration
├── ChargePriceError              a statement charge lacks the BSPx1 or PPx1 it is summed from
├── DatabaseError                 database operation failed or persistence misuse
├── EstimateCurrencyError         an estimate sums charges priced in more than one currency
└── UpstreamAPIError              an MPT API call failed upstream
    ├── UpstreamStatementError    selecting statements / streaming charges failed
    └── UpstreamSubscriptionError querying commerce subscriptions failed
```

- `ConfigurationError` is raised fast, before any work starts: missing
  `MPT_API_TOKEN`/`MPT_API_BASE_URL` (`mpt_client.py`), missing
  `MPT_DATABASE_URL` (`persistence/postgres/database.py`), unsupported DSN
  parameters (`persistence/postgres/connection.py`), and a non-TLS `sslmode`
  with Entra ID auth (`persistence/postgres/auth.py`).
- `DatabaseError` covers the persistence layer's internal-invariant guards
  (database used outside its `async with` context, an `INSERT ... RETURNING`
  producing no row); `ExtensionError` is never raised directly.
- `ChargePriceError` is raised by `accumulation.py` while charges are summed,
  when a charge has no `PPx1`, or no `BSPx1` (its sale price in the
  subscription's price currency) and no `SPx1` in its purchase currency. `SPx1`
  stands in for `BSPx1` only when the charge was sold in its purchase currency,
  since otherwise it is in another currency, and a missing price is never
  counted as 0. The error names the charge and statement, and that statement's
  `statement_processing` row is finalised as `failure`. Figures that leave a
  statement out are never persisted or pushed: in `run` the error propagates
  and the command fails before persisting any accumulated figures (additive,
  so the day is re-run with `--date` after the fix); in `recalculate` and the
  recalculating migrations `ChargeAccumulator` (`services/charges.py`) fails
  only the statement's agreement, which keeps its stored buckets and estimates,
  while the rest of the scope is rebuilt and the execution finishes as
  `completed_with_errors` with `failed_agreements`.
- `EstimateCurrencyError` is raised by `services/estimate_currency.py` when an
  estimate must not be uploaded because the subscription's charges in the run
  carry more than one purchase currency. It is handled per subscription by the
  estimate-upload boundary below.
- CLI argument validation uses Typer's own `typer.BadParameter`
  (`window.py`, `selectors.py`); it is a framework boundary, not part of the
  package hierarchy.

## Error translation at the MPT API boundary

Code that iterates or streams MPT API results catches the client library's
`MPTError` and re-raises a domain exception with the cause chain preserved
(`raise ... from exc`), so no caller depends on `mpt_api_client` exception
types:

- `services/statements.py` — statement selection → `UpstreamStatementError`
- `services/charges.py` — charge streaming → `UpstreamStatementError`
- `services/bucket_delete.py` — agreement-id resolution → `UpstreamSubscriptionError`
- `cli/commands/push_estimates_by_id.py` — subscription-id resolution → `UpstreamSubscriptionError`
- `services/estimate_currency.py` — listing the products' agreements → `UpstreamAPIError`

These sites do not log; the boundary that finally handles the error owns the
single log/notification (see below).

## What happens to an error at runtime

A `run`/`recalculate` failure crosses three layers, innermost first:

1. **Per-statement recording** — `StatementProcessingRecorder`
   (`services/execution_tracker.py`) brackets each statement's processing:
   `ChargeAccumulator` streams and sums the statement's charges inside it.
   An `Exception` escaping the bracket finalises that statement's
   `statement_processing` row as `failure` (with the error message) and
   re-raises. In a recalculate a `ChargePriceError` is then caught and the
   statement's agreement left out (see above); any other error propagates.
2. **Per-execution recording** — `ExecutionTracker` brackets the whole
   command. An `Exception` escaping the body finalises the `command_execution`
   row as `failed` (with the error in the result payload) and re-raises.
   A clean exit is `success`, or `completed_with_errors` when the execution
   handle's `has_errors` flag was set by a partial failure.

3. **Top-level boundary** — `UsageReportingPipeline._tracked`
   (`pipeline.py`) catches `Exception`, notifies MS Teams of the failure
   (message plus stacktrace, via `ExecutionNotifier`), and re-raises so the
   process exits non-zero. A `completed_with_errors` execution is also
   notified as a failure and exits with code 1 via `typer.Exit`.

Only escaping `Exception` subclasses finalise rows as failed. `BaseException`s
such as `KeyboardInterrupt` and `asyncio.CancelledError` propagate through
both recording brackets without finalising, leaving the row in its opened
state.

## When notifications are triggered

MS Teams notifications are sent by `ExecutionNotifier`
(`services/execution_notifier.py`) from the top-level boundary
(`UsageReportingPipeline._tracked`), so only the tracked commands — `run` and
`recalculate` — notify; `cleanup`, `delete`, and `push-estimates` do not.
When notifications are enabled, every tracked execution that completes or
raises an `Exception` produces exactly one card; `KeyboardInterrupt` and
`asyncio.CancelledError` produce no card:

- **Success card (✅)** — the execution finished cleanly. Includes the
  execution facts (start, duration, command line) and the run report counts.
- **Failure card (💣), unhandled exception** — an exception escaped the
  command body. Includes the error message and the stacktrace, both scrubbed
  by `sanitize_diagnostics` (SQL statements and parameters, credentials, and
  local filesystem path prefixes are redacted before the card leaves the
  host); the exception is re-raised afterward, so the process still exits
  non-zero.
- **Failure card (💣), completed with errors** — the execution finished but
  the handle's `has_errors` flag was set (partial estimate-upload failures, or
  agreements a recalculate left out because a charge could not be summed).
  Includes an error-count summary instead of a stacktrace, and the command
  exits with code 1.

Notifications are disabled entirely when `MPT_MSTEAMS_WEBHOOK_URL` is unset
or `MPT_TEAMS_NOTIFICATIONS_ENABLED` is false (`settings.py`); the notifier
then drops sends silently and the run behaves identically otherwise.

## Partial failure: estimate uploads

Estimate uploads must not let one subscription's failure abort the rest.
`PriceEstimateConsumer` (`services/estimates_uploader.py`) is the isolation
boundary: a currency-guard refusal (`EstimateCurrencyError`) is logged once at
`ERROR` without a traceback, and an upload error once with `logger.exception`;
both become a failed `UploadOutcome`. A refused estimate never reaches the
`PUT`. The run report logs each
subscription as `OK`/`FAILED`, the execution finishes as
`completed_with_errors`, and the command exits non-zero. Both the failure and
the report line reach the run's log, because the CLI configures the SDK's
logging before every command (`observability.py`); see
[deployment.md](deployment.md#logging-settings).

## Timeouts and retries

- PostgreSQL connects set an explicit `connect_timeout`
  (`persistence/postgres/connection.py`, default 10s, overridable via the DSN).
- HTTP calls to the MPT API go through the MPT Extension SDK client; this
  repository makes no direct `httpx`/`requests` calls.
- The extension performs no automatic retries. Estimate uploads may be
  re-pushed at any time (`push-estimates`) because they are absolute `PUT`s.
  A failed `run` must not simply be re-run for the same window — the
  accumulation upsert is additive and would double-count; use `recalculate`,
  which deletes the scope's buckets before re-filling them.
