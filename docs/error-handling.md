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
└── UpstreamAPIError              an MPT API call failed upstream
    ├── UpstreamStatementError    selecting statements / streaming charges failed
    └── UpstreamSubscriptionError querying commerce subscriptions failed
```

- `ConfigurationError` is raised fast, before any work starts: missing
  `MPT_API_TOKEN`/`MPT_API_BASE_URL` (`mpt_client.py`), missing
  `MPT_DATABASE_URL` (`persistence/postgres/database.py`), unsupported DSN
  parameters (`persistence/postgres/connection.py`), and a non-TLS `sslmode`
  with Entra ID auth (`persistence/postgres/auth.py`).
- `ExtensionError` is raised directly only for internal-invariant guards
  (database used outside its `async with` context, an `INSERT ... RETURNING`
  producing no row).
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

These sites do not log; the boundary that finally handles the error owns the
single log/notification (see below).

## What happens to an error at runtime

A `run`/`recalculate` failure crosses three layers, innermost first:

1. **Per-statement recording** — `StatementProcessingRecorder`
   (`services/execution_tracker.py`) brackets each statement's processing.
   An `Exception` escaping the bracket finalises that statement's
   `statement_processing` row as `failure` (with the error message) and
   re-raises.
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
  the handle's `has_errors` flag was set (partial estimate-upload failures).
  Includes an error-count summary instead of a stacktrace, and the command
  exits with code 1.

Notifications are disabled entirely when `MPT_MSTEAMS_WEBHOOK_URL` is unset
or `MPT_TEAMS_NOTIFICATIONS_ENABLED` is false (`settings.py`); the notifier
then drops sends silently and the run behaves identically otherwise.

## Partial failure: estimate uploads

Estimate uploads must not let one subscription's failure abort the rest.
`PriceEstimateConsumer` (`services/estimates_uploader.py`) is the isolation
boundary: it catches the upload error, logs it once with `logger.exception`,
and converts it into a failed `UploadOutcome`. The run report renders each
subscription as `OK`/`FAILED`, the execution finishes as
`completed_with_errors`, and the command exits non-zero.

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
