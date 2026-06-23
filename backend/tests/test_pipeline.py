import datetime as dt

import pytest
import typer
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension import pipeline
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.selectors import ProductSelector
from mpt_usage_reporting_extension.types import Month


@pytest.fixture
def stub_database(mocker):
    database = mocker.patch.object(pipeline, "SqliteDatabase").return_value.__aenter__.return_value
    database.subscription_repository = mocker.Mock(return_value=mocker.AsyncMock())
    database.agreement_repository = mocker.Mock(return_value=mocker.AsyncMock())
    return database


@pytest.fixture
def ctx(mocker, run_window):
    return RunContext(
        api_service=mocker.MagicMock(),
        window=run_window,
        product_ids=("PRD-1",),
    )


async def test_run_reports_selected_statements(mocker, capsys, stub_database, ctx):
    statements = [
        Statement({
            "id": "BILL-1",
            "status": "Issued",
            "agreement": {"id": "AGR-1"},
            "totalPP": 12.5,
        }),
        Statement({"id": "BILL-2", "status": "Cancelled"}),
    ]
    selector = mocker.patch.object(pipeline, "StatementSelector").return_value
    selector.select = mocker.AsyncMock(return_value=statements)

    await pipeline.UsageReportingPipeline(ctx).run()  # act

    out = capsys.readouterr().out
    assert "Selected 2 statement(s)" in out
    assert "BILL-1" in out
    assert "Cancelled" in out
    assert "-" in out  # missing fields render as a dash


async def test_run_reports_when_no_statements(mocker, capsys, stub_database, ctx):
    selector = mocker.patch.object(pipeline, "StatementSelector").return_value
    selector.select = mocker.AsyncMock(return_value=[])

    await pipeline.UsageReportingPipeline(ctx).run()  # act

    assert "Selected 0 statement(s)" in capsys.readouterr().out


async def test_run_exits_nonzero_when_an_upload_fails(mocker, stub_database, ctx):
    selector = mocker.patch.object(pipeline, "StatementSelector").return_value
    selector.select = mocker.AsyncMock(return_value=[])
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True))

    with pytest.raises(typer.Exit) as exc_info:
        await pipeline.UsageReportingPipeline(ctx).run()  # act

    assert exc_info.value.exit_code == 1


async def test_run_prunes_both_accumulation_tables(mocker, stub_database, ctx):
    selector = mocker.patch.object(pipeline, "StatementSelector").return_value
    selector.select = mocker.AsyncMock(return_value=[])
    anchor = dt.datetime.now(tz=dt.UTC).date()  # cleanup anchors on the current UTC month

    await pipeline.UsageReportingPipeline(ctx).run()  # act

    subscription_prune = stub_database.subscription_repository.return_value.prune
    agreement_prune = stub_database.agreement_repository.return_value.prune
    subscription_prune.assert_awaited_once_with(anchor.year, Month(anchor.month))
    agreement_prune.assert_awaited_once_with(anchor.year, Month(anchor.month))


async def test_recalculate_deletes_then_prunes(mocker, stub_database, ctx):
    selector = mocker.patch.object(pipeline, "StatementSelector").return_value
    selector.select = mocker.AsyncMock(return_value=[])
    deleter = mocker.patch.object(pipeline, "BucketDeleter").return_value
    deleter.delete = mocker.AsyncMock()
    anchor = dt.datetime.now(tz=dt.UTC).date()  # cleanup anchors on the current UTC month

    await pipeline.UsageReportingPipeline(ctx).recalculate(None)  # act

    # a None scope is expanded to the configured products, not a global wipe
    deleter.delete.assert_awaited_once_with(ProductSelector("PRD-1"))
    # recalculate now performs a regular run, so retention pruning runs too
    stub_database.subscription_repository.return_value.prune.assert_awaited_once_with(
        anchor.year, Month(anchor.month)
    )
    stub_database.agreement_repository.return_value.prune.assert_awaited_once_with(
        anchor.year, Month(anchor.month)
    )


async def test_recalculate_deletes_the_given_scope(mocker, stub_database, ctx):
    selector = mocker.patch.object(pipeline, "StatementSelector").return_value
    selector.select = mocker.AsyncMock(return_value=[])
    deleter = mocker.patch.object(pipeline, "BucketDeleter").return_value
    deleter.delete = mocker.AsyncMock()
    scope = ProductSelector("PRD-9")

    await pipeline.UsageReportingPipeline(ctx).recalculate(scope)  # act

    deleter.delete.assert_awaited_once_with(scope)
