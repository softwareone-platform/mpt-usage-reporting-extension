from typer.testing import CliRunner

from mpt_usage_reporting_extension import cli

runner = CliRunner()


def test_cli_help_shows_command():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "Report billing subscription usage" in result.stdout


def test_cli_reports_not_implemented():
    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    assert "Not implemented" in result.stdout


def test_main_invokes_app(mocker):
    mocked_app = mocker.patch.object(cli, "app")

    cli.main()  # act

    mocked_app.assert_called_once_with()
