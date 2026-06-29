from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.selectors import SellerSelector


def _patch_pipeline(mocker):
    mocker.patch.object(cli.commands.recalculate, "build_service")
    mocker.patch.object(cli.commands.recalculate.ExtensionSettings, "load")
    pipeline_cls = mocker.patch.object(cli.commands.recalculate, "UsageReportingPipeline")
    pipeline_cls.return_value.recalculate = mocker.AsyncMock()
    return pipeline_cls


def test_recalculate_invokes_pipeline_with_scope(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(cli.app, ["recalculate", "--seller-id", "SEL-1"])

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    recalc.assert_awaited_once()
    assert recalc.await_args.args[0] == SellerSelector("SEL-1")
    assert recalc.await_args.args[1] == {"product_id": None, "seller_id": "SEL-1"}
    ctx = pipeline_cls.call_args.args[0]
    assert ctx.seller_id == "SEL-1"
    assert ctx.window is None


def test_recalculate_no_scope_passes_none(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(cli.app, ["recalculate"])

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    assert recalc.await_args.args[0] is None


def test_recalculate_rejects_two_scopes(mocker, runner):
    mocker.patch.object(cli.commands.recalculate, "build_service")
    pipeline_cls = mocker.patch.object(cli.commands.recalculate, "UsageReportingPipeline")

    result = runner.invoke(
        cli.app, ["recalculate", "--product-id", "PRD-1", "--seller-id", "SEL-1"]
    )

    assert result.exit_code != 0
    pipeline_cls.assert_not_called()
