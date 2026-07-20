"""Bootstrap SDK observability for CLI (cronjob) executions.

The SDK initializes tracing only inside its serve runtime (``create_runtime_app``). The
extension's Typer CLI runs outside that runtime, so it bootstraps observability itself from
the same environment variables the SDK serve path consumes.
"""

import os

from mpt_extension_sdk.observability.bootstrap import ObservabilityBootstrap
from mpt_extension_sdk.observability.config import ObservabilityConfig
from mpt_extension_sdk.settings.base import BaseSettings


def setup_observability() -> None:
    """Initialize the SDK tracing provider and instrumentation for CLI runs.

    Delegates to the SDK's idempotent bootstrap, so calling it in a process that already
    serves the extension through the SDK runtime is a no-op.
    """
    ObservabilityBootstrap.bootstrap(load_observability_config())


def load_observability_config() -> ObservabilityConfig:
    """Build the SDK observability config from environment variables.

    Mirrors ``ObservabilityConfig.from_runtime_settings`` without loading the full runtime
    settings, which require serve-only variables the cronjob does not define.

    Returns:
        The resolved observability configuration.
    """
    connection_string = os.getenv("SDK_APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    return ObservabilityConfig(
        enabled=BaseSettings.bool_env("SDK_OBSERVABILITY_ENABLED", default=True),
        exporters=_configured_exporters(connection_string),
        service_name=os.getenv("SDK_OTEL_SERVICE_NAME", ""),
        applicationinsights_connection_string=connection_string,
    )


def _configured_exporters(connection_string: str) -> tuple[str, ...]:
    """Derive the exporter names whose destination configuration is present."""
    exporters = []
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "") or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", ""
    )
    if otlp_endpoint:
        exporters.append("otlp")
    if connection_string:
        exporters.append("azure_monitor")
    return tuple(exporters)
