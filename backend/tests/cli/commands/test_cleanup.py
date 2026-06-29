import datetime as dt

from mpt_usage_reporting_extension import cli


def test_cleanup_invokes_do_cleanup(mocker, runner):
    do_cleanup = mocker.patch.object(cli.commands.cleanup, "do_cleanup")

    result = runner.invoke(cli.app, ["cleanup", "--date", "2026-06-01"])

    assert result.exit_code == 0
    do_cleanup.assert_awaited_once()
    anchor, parameters = do_cleanup.await_args.args
    assert anchor == dt.date(2026, 6, 1)
    assert parameters["date"].date() == dt.date(2026, 6, 1)
