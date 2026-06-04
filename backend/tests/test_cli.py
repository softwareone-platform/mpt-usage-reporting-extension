from mpt_api_client.resources.billing.statements import Statement
from typer.testing import CliRunner

from mpt_usage_reporting_extension import cli

runner = CliRunner()


def test_cli_help_shows_command():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "Report billing subscription usage" in result.stdout


def test_cli_reports_not_implemented():
    result = runner.invoke(cli.app, ["billing-subscription-usage"])

    assert result.exit_code == 0
    assert "Not implemented" in result.stdout


def test_run_reports_selected_statements(mocker):
    mocker.patch.object(cli, "build_client")
    mocker.patch.object(cli.ExtensionSettings, "load")
    statements = [
        Statement({
            "id": "BILL-1",
            "status": "Issued",
            "agreement": {"id": "AGR-1"},
            "totalPP": 12.5,
        }),
        Statement({"id": "BILL-2", "status": "Cancelled"}),
    ]
    selector = mocker.patch.object(cli, "StatementSelector").return_value
    selector.select.side_effect = lambda ctx: ctx.statements.extend(statements)

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])  # act

    assert result.exit_code == 0
    assert "Selected 2 statement(s)" in result.stdout
    assert "BILL-1" in result.stdout
    assert "Cancelled" in result.stdout
    assert "-" in result.stdout  # missing fields render as a dash


def test_run_reports_when_no_statements(mocker):
    mocker.patch.object(cli, "build_client")
    mocker.patch.object(cli.ExtensionSettings, "load")
    mocker.patch.object(cli, "StatementSelector")

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])  # act

    assert result.exit_code == 0
    assert "Selected 0 statement(s)" in result.stdout


def test_main_invokes_app(mocker):
    mocked_app = mocker.patch.object(cli, "app")

    cli.main()  # act

    mocked_app.assert_called_once_with()
