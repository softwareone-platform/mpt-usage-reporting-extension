from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    SellerSelector,
    SubscriptionSelector,
)


def _patch_pipeline(mocker):
    mocker.patch.object(cli.commands.recalculate, "build_service")
    mocker.patch.object(cli.commands.recalculate.ExtensionSettings, "load")
    pipeline_cls = mocker.patch.object(cli.commands.recalculate, "UsageReportingPipeline")
    pipeline_cls.return_value.recalculate = mocker.AsyncMock()
    return pipeline_cls


def test_recalculate_invokes_pipeline_with_scope(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(
        cli.app,
        [
            "recalculate",
            "--from-date",
            "2026-06-01",
            "--till-date",
            "2026-06-10",
            "--seller-id",
            "SEL-1",
        ],
    )

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    recalc.assert_awaited_once()
    assert recalc.await_args.args[0] == SellerSelector("SEL-1")
    assert recalc.await_args.args[1] == {"product_id": None, "seller_id": "SEL-1"}
    ctx = pipeline_cls.call_args.args[0]
    assert ctx.seller_id == "SEL-1"
    assert ctx.window is not None


def test_recalculate_with_agreement_scope(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(
        cli.app,
        [
            "recalculate",
            "--from-date",
            "2026-06-01",
            "--till-date",
            "2026-06-10",
            "--agreement-id",
            "AGR-1",
        ],
    )

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    assert recalc.await_args.args[0] == AgreementSelector("AGR-1")


def test_recalculate_with_subscription_scope(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(
        cli.app,
        [
            "recalculate",
            "--from-date",
            "2026-06-01",
            "--till-date",
            "2026-06-10",
            "--subscription-id",
            "SUB-1",
        ],
    )

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    assert recalc.await_args.args[0] == SubscriptionSelector("SUB-1")


def test_recalculate_no_scope_passes_none(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(
        cli.app,
        ["recalculate", "--from-date", "2026-06-01", "--till-date", "2026-06-10"],
    )

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    assert recalc.await_args.args[0] is None


def test_recalculate_dry_run_passes_true(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(
        cli.app,
        [
            "recalculate",
            "--from-date",
            "2026-06-01",
            "--till-date",
            "2026-06-10",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    recalc = pipeline_cls.return_value.recalculate
    assert recalc.await_args.kwargs["dry_run"] is True


def test_recalculate_rejects_two_scopes(mocker, runner):
    mocker.patch.object(cli.commands.recalculate, "build_service")
    pipeline_cls = mocker.patch.object(cli.commands.recalculate, "UsageReportingPipeline")

    result = runner.invoke(
        cli.app,
        [
            "recalculate",
            "--from-date",
            "2026-06-01",
            "--till-date",
            "2026-06-10",
            "--product-id",
            "PRD-1",
            "--seller-id",
            "SEL-1",
        ],
    )

    assert result.exit_code != 0
    pipeline_cls.assert_not_called()


def test_recalculate_requires_from_and_till_date(mocker, runner):
    pipeline_cls = _patch_pipeline(mocker)

    result = runner.invoke(cli.app, ["recalculate", "--seller-id", "SEL-1"])

    assert result.exit_code != 0
    pipeline_cls.assert_not_called()
