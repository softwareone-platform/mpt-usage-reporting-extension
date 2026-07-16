# External Integrations

This document lists the external systems the extension integrates with, their
purpose, and how they authenticate. The full environment-variable reference
lives in [deployment.md](deployment.md); this document is the index and does not
duplicate those tables.

## Integrations

| System | Purpose | Auth | Configuration | Code |
| --- | --- | --- | --- | --- |
| SoftwareOne Marketplace (MPT) API | Select billing statements, stream charges, and read/sync agreements | Bearer token | `MPT_API_BASE_URL`, `MPT_API_TOKEN`, `MPT_PRODUCTS_IDS` | [`backend/mpt_usage_reporting_extension/mpt_client.py`](../backend/mpt_usage_reporting_extension/mpt_client.py), `services/statements.py`, `services/charges.py` |
| MPT Extension SDK runtime | Hosts the extension app and provides settings and context | Extension credentials | `SDK_EXTENSION_API_KEY`, `SDK_EXTENSION_ID`, `SDK_EXTENSION_URL` | provided by `mpt-extension-sdk` |
| Airtable (optional) | `mpt-tool` storage backend when enabled | API key | `MPT_TOOL_STORAGE_TYPE`, `MPT_TOOL_STORAGE_AIRTABLE_API_KEY`, `MPT_TOOL_STORAGE_AIRTABLE_BASE_ID`, `MPT_TOOL_STORAGE_AIRTABLE_TABLE_NAME` | `mpt-tool` |
| Jaeger / OpenTelemetry | Distributed tracing (Jaeger locally, Azure Monitor in deployed environments) | none (local endpoint) / connection string | `SDK_OBSERVABILITY_ENABLED`, `SDK_OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `SDK_APPLICATIONINSIGHTS_CONNECTION_STRING` | [`backend/mpt_usage_reporting_extension/observability.py`](../backend/mpt_usage_reporting_extension/observability.py) and the SDK runtime |
| MS Teams (optional) | Notify each `run`/`recalculate` execution outcome as an Adaptive Card | Workflows webhook URL | `MPT_MSTEAMS_WEBHOOK_URL`, `MPT_TEAMS_NOTIFICATIONS_ENABLED` | [`backend/mpt_usage_reporting_extension/services/execution_notifier.py`](../backend/mpt_usage_reporting_extension/services/execution_notifier.py) via `mpt-extension-contrib-custom-notifications` |

## Notes

- The CLI's `run` command reads billing data from the MPT API
  (`GET /public/v1/billing/statements` and the per-statement charges streaming
  endpoint); `MPT_API_TOKEN` must be an operations-scoped token and is provided
  via `backend/.env.local` for local runs.
- Airtable is only used when `MPT_TOOL_STORAGE_TYPE=airtable`; with the default
  `local` storage the Airtable variables can remain unset.
- OpenTelemetry tracing is bootstrapped by the SDK runtime in serve mode and by the
  CLI callback for every command (cronjobs included); an exporter only activates when
  its destination variable is set (OTLP endpoint or Application Insights connection
  string).
- Teams notifications are a no-op when `MPT_MSTEAMS_WEBHOOK_URL` is unset or
  `MPT_TEAMS_NOTIFICATIONS_ENABLED=false`; webhook transport errors are logged
  and never fail the run.
