import datetime as dt

from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.window import resolve_window


def test_run_invokes_pipeline(mocker, runner):
    mocker.patch.object(cli.commands.run, "build_service")
    mocker.patch.object(cli.commands.run.ExtensionSettings, "load")
    pipeline_cls = mocker.patch.object(cli.commands.run, "UsageReportingPipeline")
    pipeline_cls.return_value.run = mocker.AsyncMock()

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])

    assert result.exit_code == 0
    pipeline_cls.return_value.run.assert_awaited_once()
    ctx = pipeline_cls.call_args.args[0]
    assert ctx.window == resolve_window(dt.date(2026, 6, 1), None, None)
