from mpt_usage_reporting_extension import cli


def test_cli_help_shows_command(runner):
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "Report MPT billing subscription usage" in result.stdout
    assert "run" in result.stdout
    assert "cleanup" in result.stdout


def test_main_invokes_app(mocker):
    mocked_app = mocker.patch("mpt_usage_reporting_extension.cli._app.app")

    cli.main()  # act

    mocked_app.assert_called_once_with()
