from mpt_usage_reporting_extension import cli


def test_run_invokes_pipeline(mocker, runner):
    mocker.patch.object(cli.commands.run, "build_service")
    mocker.patch.object(cli.commands.run.ExtensionSettings, "load")
    pipeline_cls = mocker.patch.object(cli.commands.run, "UsageReportingPipeline")
    pipeline_cls.return_value.run = mocker.AsyncMock()

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])

    assert result.exit_code == 0
    pipeline_cls.return_value.run.assert_awaited_once()
