# External Integrations

This document lists the external systems the extension integrates with, their
purpose, and how they authenticate. The full environment-variable reference
lives in [deployment.md](deployment.md); this document is the index and does not
duplicate those tables.

## Integrations

| System | Purpose | Auth | Configuration | Code |
| --- | --- | --- | --- | --- |
| SoftwareOne Marketplace (MPT) API | Select billing statements, stream charges, and read/sync agreements | Bearer token | `MPT_API_BASE_URL`, `MPT_API_TOKEN`, `MPT_PRODUCTS_IDS` | [`backend/mpt_usage_reporting_extension/mpt_client.py`](../backend/mpt_usage_reporting_extension/mpt_client.py), `statements.py`, `charges.py` |
| MPT Extension SDK runtime | Hosts the extension app (event/API/plug routes) and provides settings and context | Extension credentials | `SDK_EXTENSION_API_KEY`, `SDK_EXTENSION_ID`, `SDK_EXTENSION_URL` | provided by `mpt-extension-sdk` |
| Airtable (optional) | `mpt-tool` storage backend when enabled | API key | `MPT_TOOL_STORAGE_TYPE`, `MPT_TOOL_STORAGE_AIRTABLE_API_KEY`, `MPT_TOOL_STORAGE_AIRTABLE_BASE_ID`, `MPT_TOOL_STORAGE_AIRTABLE_TABLE_NAME` | `mpt-tool` |
| Jaeger / OpenTelemetry (local) | Distributed tracing in local/dev mode | none (local endpoint) | `SDK_OTEL_SERVICE_NAME`, `SDK_OTEL_EXPORTERS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | enabled in `--local` mode |

## Notes

- The CLI's `run` command reads billing data from the MPT API
  (`GET /public/v1/billing/statements` and the per-statement charges streaming
  endpoint); `MPT_API_TOKEN` must be an operations-scoped token and is provided
  via `backend/.env.local` for local runs.
- Airtable is only used when `MPT_TOOL_STORAGE_TYPE=airtable`; with the default
  `local` storage the Airtable variables can remain unset.
- Jaeger/OpenTelemetry tracing is wired only in `--local` mode (`make run-local`).
