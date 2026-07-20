from mpt_usage_reporting_extension import cli


def test_cli_help_shows_command(runner):
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "Report MPT billing subscription usage" in result.stdout
    assert "run" in result.stdout
    assert "cleanup" in result.stdout


def test_cli_registers_all_commands(runner):
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "delete" in result.stdout
    assert "recalculate" in result.stdout
    assert "status" in result.stdout
    assert "push-estimates" in result.stdout


def test_push_estimates_registers_subcommands(runner):
    result = runner.invoke(cli.app, ["push-estimates", "--help"])

    assert result.exit_code == 0
    assert "by-id" in result.stdout
    assert "by-updated-at" in result.stdout


def test_main_invokes_app(mocker):
    mocked_app = mocker.patch("mpt_usage_reporting_extension.cli._app.app")

    cli.main()  # act

    mocked_app.assert_called_once_with()


def test_command_invocation_bootstraps_observability(mocker, runner, mock_setup_observability):
    mocker.patch.object(cli.commands.run, "build_service")
    mocker.patch.object(cli.commands.run.ExtensionSettings, "load")
    pipeline_cls = mocker.patch.object(cli.commands.run, "UsageReportingPipeline")
    pipeline_cls.return_value.run = mocker.AsyncMock()

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])

    assert result.exit_code == 0
    mock_setup_observability.assert_called_once_with()
