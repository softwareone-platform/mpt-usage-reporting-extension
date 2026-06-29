from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.selectors import ProductSelector


def test_delete_invokes_with_scope(mocker, runner):
    mocker.patch.object(cli.commands.delete, "build_service")
    inner = mocker.patch.object(cli.commands.delete, "_delete")

    result = runner.invoke(cli.app, ["delete", "--product-id", "PRD-1"])

    assert result.exit_code == 0
    inner.assert_awaited_once()
    assert inner.await_args.args[1] == ProductSelector("PRD-1")


def test_delete_rejects_two_filters(mocker, runner):
    mocker.patch.object(cli.commands.delete, "build_service")
    inner = mocker.patch.object(cli.commands.delete, "_delete")

    result = runner.invoke(cli.app, ["delete", "--product-id", "PRD-1", "--seller-id", "SEL-1"])

    assert result.exit_code != 0
    inner.assert_not_called()


def test_delete_rejects_no_filter(mocker, runner):
    mocker.patch.object(cli.commands.delete, "build_service")
    inner = mocker.patch.object(cli.commands.delete, "_delete")

    result = runner.invoke(cli.app, ["delete"])

    assert result.exit_code != 0
    inner.assert_not_called()


def test_delete_deletes_scope_buckets(mocker, runner):
    mocker.patch.object(cli.commands.delete, "build_service")
    database = mocker.MagicMock()
    database.__aenter__ = mocker.AsyncMock(return_value=database)
    database.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch.object(cli.commands.delete, "resolve_db_path")
    mocker.patch.object(cli.commands.delete, "SqliteDatabase", return_value=database)
    database.execution_repository = mocker.Mock(return_value=mocker.AsyncMock())
    deleter = mocker.patch.object(cli.commands.delete, "BucketDeleter").return_value
    deleter.delete = mocker.AsyncMock()

    result = runner.invoke(cli.app, ["delete", "--product-id", "PRD-1"])

    assert result.exit_code == 0
    deleter.delete.assert_awaited_once_with(ProductSelector("PRD-1"))
