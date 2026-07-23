import pytest
from mpt_extension_sdk.observability.config import ObservabilityConfig

from mpt_usage_reporting_extension.observability import (
    load_observability_config,
    setup_observability,
)

_OBSERVABILITY_ENV_VARS = (
    "SDK_OBSERVABILITY_ENABLED",
    "SDK_OTEL_SERVICE_NAME",
    "SDK_APPLICATIONINSIGHTS_CONNECTION_STRING",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clean_observability_env(monkeypatch):
    for env_key in _OBSERVABILITY_ENV_VARS:
        monkeypatch.delenv(env_key, raising=False)


@pytest.mark.parametrize(
    ("env", "expected_config"),
    [
        (
            {},
            ObservabilityConfig(
                enabled=True,
                exporters=(),
                service_name="",
                applicationinsights_connection_string="",
            ),
        ),
        (
            {"SDK_OBSERVABILITY_ENABLED": "false"},
            ObservabilityConfig(
                enabled=False,
                exporters=(),
                service_name="",
                applicationinsights_connection_string="",
            ),
        ),
        (
            {
                "SDK_OTEL_SERVICE_NAME": "Swo.Extension.UsageReporting",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4318",
            },
            ObservabilityConfig(
                enabled=True,
                exporters=("otlp",),
                service_name="Swo.Extension.UsageReporting",
                applicationinsights_connection_string="",
            ),
        ),
        (
            {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://jaeger:4318/v1/traces"},
            ObservabilityConfig(
                enabled=True,
                exporters=("otlp",),
                service_name="",
                applicationinsights_connection_string="",
            ),
        ),
        (
            {"SDK_APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=fake-key"},
            ObservabilityConfig(
                enabled=True,
                exporters=("azure_monitor",),
                service_name="",
                applicationinsights_connection_string="InstrumentationKey=fake-key",
            ),
        ),
        (
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4318",
                "SDK_APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=fake-key",
            },
            ObservabilityConfig(
                enabled=True,
                exporters=("otlp", "azure_monitor"),
                service_name="",
                applicationinsights_connection_string="InstrumentationKey=fake-key",
            ),
        ),
    ],
)
def test_load_observability_config(monkeypatch, env, expected_config):
    for env_key, env_value in env.items():
        monkeypatch.setenv(env_key, env_value)

    result = load_observability_config()

    assert result == expected_config


def test_setup_observability_bootstraps_sdk(mocker):
    bootstrap = mocker.patch(
        "mpt_usage_reporting_extension.observability.ObservabilityBootstrap.bootstrap",
        autospec=True,
    )

    setup_observability()  # act

    bootstrap.assert_called_once_with(load_observability_config())
