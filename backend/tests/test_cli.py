import pytest
from typer.testing import CliRunner

from mpt_usage_reporting_extension import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help_shows_command(runner):
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "Report MPT billing subscription usage" in result.stdout
    assert "run" in result.stdout


def test_run_invokes_pipeline(mocker, runner):
    mocker.patch.object(cli, "build_service")
    mocker.patch.object(cli.ExtensionSettings, "load")
    pipeline_cls = mocker.patch.object(cli, "UsageReportingPipeline")
    pipeline_cls.return_value.run = mocker.AsyncMock()

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])

    assert result.exit_code == 0
    pipeline_cls.return_value.run.assert_awaited_once()


def test_main_invokes_app(mocker):
    mocked_app = mocker.patch.object(cli, "app")

    cli.main()  # act

    mocked_app.assert_called_once_with()
