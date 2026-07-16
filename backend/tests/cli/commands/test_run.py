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
    run_mock = pipeline_cls.return_value.run
    run_mock.assert_awaited_once()
    parameters = run_mock.await_args.args[0]
    assert parameters["date"].date() == dt.date(2026, 6, 1)
    assert parameters["from_date"] is None
    assert parameters["till_date"] is None
    ctx = pipeline_cls.call_args.args[0]
    assert ctx.window == resolve_window(dt.date(2026, 6, 1), None, None)


def test_run_passes_notifier_to_pipeline(mocker, runner):
    mocker.patch.object(cli.commands.run, "build_service")
    mocker.patch.object(cli.commands.run.ExtensionSettings, "load")
    notifier = mocker.patch.object(cli.commands.run, "build_execution_notifier").return_value
    pipeline_cls = mocker.patch.object(cli.commands.run, "UsageReportingPipeline")
    pipeline_cls.return_value.run = mocker.AsyncMock()

    result = runner.invoke(cli.app, ["run", "--date", "2026-06-01"])

    assert result.exit_code == 0
    assert pipeline_cls.call_args.args[0].notifier is notifier
