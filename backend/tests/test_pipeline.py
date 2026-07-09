import datetime as dt

import pytest
import typer
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension import pipeline
from mpt_usage_reporting_extension.accumulation import StatementChargeFilter
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    ProductSelector,
    SubscriptionSelector,
)
from mpt_usage_reporting_extension.services.bucket_delete import DeleteOutcome
from mpt_usage_reporting_extension.types import Month


@pytest.fixture
def stub_database(mocker):
    mocker.patch.object(pipeline, "resolve_database_url")
    database = mocker.patch.object(
        pipeline, "PostgresDatabase"
    ).return_value.__aenter__.return_value
    database.subscription_repository = mocker.Mock(return_value=mocker.AsyncMock())
    database.agreement_repository = mocker.Mock(return_value=mocker.AsyncMock())
    database.execution_repository = mocker.Mock(return_value=mocker.AsyncMock())
    database.statement_processing_repository = mocker.Mock(return_value=mocker.AsyncMock())
    return database


@pytest.fixture
def ctx(mocker, run_window, notifier):
    return RunContext(
        api_service=mocker.MagicMock(),
        window=run_window,
        product_ids=("PRD-1",),
        notifier=notifier,
    )


@pytest.fixture
def notifier(mocker):
    return mocker.AsyncMock()


@pytest.fixture
def usage(ctx):
    return pipeline.UsageReportingPipeline(ctx)


@pytest.fixture
def selector(mocker):
    stub = mocker.patch.object(pipeline, "StatementSelector").return_value
    stub.select = mocker.AsyncMock(return_value=[])
    return stub


@pytest.fixture
def deleter(mocker):
    stub = mocker.patch.object(pipeline, "BucketDeleter").return_value
    stub.delete = mocker.AsyncMock(return_value=DeleteOutcome())
    return stub


async def test_run_reports_selected_statements(mocker, capsys, stub_database, usage, selector):
    selector.select = mocker.AsyncMock(
        return_value=[
            Statement({
                "id": "BILL-1",
                "status": "Issued",
                "agreement": {"id": "AGR-1"},
                "totalPP": 12.5,
            }),
            Statement({"id": "BILL-2", "status": "Cancelled"}),
        ]
    )

    await usage.run({})  # act

    out = capsys.readouterr().out
    assert "Selected 2 statement(s)" in out
    assert "BILL-1" in out
    assert "Cancelled" in out
    assert "-" in out  # missing fields render as a dash


async def test_run_reports_when_no_statements(capsys, stub_database, usage, selector):
    await usage.run({})  # act

    assert "Selected 0 statement(s)" in capsys.readouterr().out


async def test_run_exits_nonzero_when_an_upload_fails(mocker, stub_database, usage, selector):
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True, failed_count=2))

    with pytest.raises(typer.Exit) as exc_info:
        await usage.run({})  # act

    assert exc_info.value.exit_code == 1


async def test_run_prunes_both_accumulation_tables(stub_database, usage, selector):
    anchor = dt.datetime.now(tz=dt.UTC).date()  # cleanup anchors on the current UTC month

    await usage.run({})  # act

    subscription_prune = stub_database.subscription_repository.return_value.prune
    agreement_prune = stub_database.agreement_repository.return_value.prune
    subscription_prune.assert_awaited_once_with(anchor.year, Month(anchor.month))
    agreement_prune.assert_awaited_once_with(anchor.year, Month(anchor.month))


async def test_run_notifies_success_with_run_report(stub_database, usage, selector, notifier):
    await usage.run({})  # act

    notifier.notify_success.assert_called_once()
    command_run, report = notifier.notify_success.call_args.args
    assert command_run.name == "run"
    assert command_run.command
    assert report == {"statements": 0, "accumulations": 0, "estimates_failed": 0}
    notifier.notify_failure.assert_not_called()


async def test_run_notifies_failure_when_uploads_fail(
    mocker, stub_database, usage, selector, notifier
):
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True, failed_count=2))

    with pytest.raises(typer.Exit):
        await usage.run({})  # act

    notifier.notify_failure.assert_called_once()
    command_run, error = notifier.notify_failure.call_args.args
    assert command_run.name == "run"
    assert "estimates_failed=2" in error
    notifier.notify_success.assert_not_called()


async def test_run_notifies_failure_with_stacktrace_and_reraises(
    mocker, stub_database, usage, selector, notifier
):
    selector.select = mocker.AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await usage.run({})  # act

    command_run, error, stacktrace = notifier.notify_failure.call_args.args
    assert command_run.name == "run"
    assert error == "boom"
    assert "RuntimeError: boom" in stacktrace


async def test_recalculate_notifies_success(stub_database, usage, selector, deleter, notifier):
    deleter.delete.return_value = DeleteOutcome()
    deleter.statement_agreements = frozenset(("AGR-1",))

    await usage.recalculate(None, {})  # act

    notifier.notify_success.assert_called_once()
    command_run = notifier.notify_success.call_args.args[0]
    assert command_run.name == "recalculate"


async def test_recalculate_deletes_then_prunes(
    mocker, stub_database, usage, selector, deleter, ctx
):
    deleter.delete.return_value = DeleteOutcome()
    deleter.statement_agreements = frozenset(("AGR-1",))
    anchor = dt.datetime.now(tz=dt.UTC).date()  # cleanup anchors on the current UTC month

    await usage.recalculate(None, {})  # act

    # a None scope is expanded to the configured products, not a global wipe
    deleter.delete.assert_awaited_once_with(ProductSelector("PRD-1"))
    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ("AGR-1",))
    # recalculate still prunes for retention after the re-fill
    stub_database.subscription_repository.return_value.prune.assert_awaited_once_with(
        anchor.year, Month(anchor.month)
    )
    stub_database.agreement_repository.return_value.prune.assert_awaited_once_with(
        anchor.year, Month(anchor.month)
    )


async def test_recalculate_exits_on_upload_failure(mocker, stub_database, usage, selector, deleter):
    deleter.delete.return_value = DeleteOutcome()
    deleter.statement_agreements = frozenset(("AGR-1",))
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True, failed_count=1))

    with pytest.raises(typer.Exit) as exc_info:
        await usage.recalculate(None, {})  # act

    assert exc_info.value.exit_code == 1


async def test_recalculate_dry_run_runs_reads_but_skips_mutations(
    mocker,
    capsys,
    stub_database,
    usage,
    selector,
    deleter,
    ctx,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-1"])
    deleter.statement_agreements = frozenset(("AGR-1",))
    mocker.patch.object(pipeline, "ChargeStreamer")
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-1")
        )
    )
    subscription_repo = stub_database.subscription_repository.return_value
    subscription_repo.get = mocker.AsyncMock(return_value=None)
    subscription_api = ctx.api_service.subscriptions
    subscription_api.update = mocker.AsyncMock()

    await usage.recalculate(ProductSelector("PRD-1"), {}, dry_run=True)  # act

    deleter.delete.assert_awaited_once_with(ProductSelector("PRD-1"))
    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ("AGR-1",))
    assert subscription_repo.get.await_count > 0  # estimate calculation still performs DB reads
    subscription_repo.delete.assert_not_called()
    subscription_repo.prune.assert_not_called()
    agreement_repo = stub_database.agreement_repository.return_value
    agreement_repo.delete.assert_not_called()
    agreement_repo.prune.assert_not_called()
    subscription_api.update.assert_not_called()
    assert "Dry run: running recalculate in read-only mode" in capsys.readouterr().out


async def test_recalculate_dry_run_still_processes_upload_subscription_ids(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-1"])
    deleter.statement_agreements = frozenset(("AGR-1",))
    mocker.patch.object(pipeline, "ChargeStreamer")
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-1")
        )
    )
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False, render=mocker.Mock())
    )

    await usage.recalculate(ProductSelector("PRD-1"), {}, dry_run=True)  # act

    ids = list(uploader.update.await_args.args[0])
    assert ids == ["SUB-1"]


async def test_recalculate_deletes_the_given_scope(stub_database, usage, selector, deleter, ctx):
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-1"])
    scope = ProductSelector("PRD-9")

    await usage.recalculate(scope, {})  # act

    deleter.delete.assert_awaited_once_with(scope)
    assert ctx.charge_filter is None


async def test_recalculate_persists_reset_only(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-1"])
    deleter.statement_agreements = frozenset(("AGR-1",))
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-1"),
            charge_accumulation_factory("SUB-2", agreement_id="AGR-2"),
        )
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = accumulate
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(ProductSelector("PRD-1"), {})  # act

    persisted, agreement_ids = persister.persist.call_args.args
    assert [bucket.subscription_id for bucket in persisted] == ["SUB-1"]
    assert agreement_ids == frozenset(("AGR-1",))
    assert accumulate.await_args.args[1].subscription_ids == frozenset(("SUB-1",))


async def test_recalculate_agreement_scope(stub_database, usage, selector, deleter, ctx):
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-7"])
    deleter.statement_agreements = frozenset(("AGR-7",))

    await usage.recalculate(AgreementSelector("AGR-7"), {})  # act

    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ("AGR-7",))


async def test_recalculate_subscription_scope(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-1"])
    mocker.patch.object(pipeline, "ChargeStreamer")
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-1"),
            charge_accumulation_factory("SUB-2", agreement_id="AGR-1"),
        )
    )
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(SubscriptionSelector("SUB-1"), {})  # act

    persisted, agreement_ids = persister.persist.call_args.args
    assert [bucket.subscription_id for bucket in persisted] == ["SUB-1"]
    # empty agreement set => the shared agreement bucket is left untouched
    assert agreement_ids == frozenset()


async def test_recalculate_keeps_agreement_only_resets(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome(agreements=["AGR-7"])
    deleter.statement_agreements = frozenset(("AGR-7",))
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("agreement_additional_AGR-7", agreement_id="AGR-7"),
            charge_accumulation_factory("SUB-2", agreement_id="AGR-2"),
        )
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = accumulate
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(AgreementSelector("AGR-7"), {})  # act

    persisted, agreement_ids = persister.persist.call_args.args
    assert [bucket.agreement_id for bucket in persisted] == ["AGR-7"]
    assert agreement_ids == frozenset(("AGR-7",))
    assert accumulate.await_args.args[1] is None


async def test_recalculate_product_scope_bootstraps_empty_database(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome()
    deleter.statement_agreements = frozenset(("AGR-1", "AGR-2"))
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-1"),
            charge_accumulation_factory("SUB-2", agreement_id="AGR-2"),
        )
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = accumulate
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(ProductSelector("PRD-1"), {})  # act

    persisted, agreement_ids = persister.persist.call_args.args
    assert [bucket.subscription_id for bucket in persisted] == ["SUB-1", "SUB-2"]
    assert agreement_ids == frozenset(("AGR-1", "AGR-2"))
    assert accumulate.await_args.args[1] is None  # nothing was deleted, so no charge filter


async def test_recalculate_subscription_scope_bootstraps_empty_database(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    ctx,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome()
    deleter.statement_agreements = frozenset()
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-1"),
            charge_accumulation_factory("SUB-2", agreement_id="AGR-1"),
        )
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = accumulate
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(SubscriptionSelector("SUB-1"), {})  # act

    persisted, agreement_ids = persister.persist.call_args.args
    assert [bucket.subscription_id for bucket in persisted] == ["SUB-1"]
    assert agreement_ids == frozenset()
    assert accumulate.await_args.args[1].subscription_ids == frozenset(("SUB-1",))
    # no stored agreements to narrow by, so statements fall back to the product scope
    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ())


async def test_recalculate_agreement_scope_with_intact_agreement_bucket(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    ctx,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.delete.return_value = DeleteOutcome()
    deleter.statement_agreements = frozenset(("AGR-7",))
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-7", agreement_id="AGR-7"),
            charge_accumulation_factory("SUB-2", agreement_id="AGR-2"),
        )
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = accumulate
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(AgreementSelector("AGR-7"), {})  # act

    persisted, agreement_ids = persister.persist.call_args.args
    assert [bucket.agreement_id for bucket in persisted] == ["AGR-7"]
    assert agreement_ids == frozenset(("AGR-7",))
    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ("AGR-7",))


async def test_recalculate_restores_previous_subscription_filter_on_refill_failure(
    mocker, stub_database, usage, ctx, selector, deleter
):
    previous_filter = StatementChargeFilter(("PREVIOUS",))
    ctx.charge_filter = previous_filter
    deleter.delete.return_value = DeleteOutcome(subscriptions=["SUB-1"])
    deleter.statement_agreements = frozenset()
    mocker.patch.object(pipeline, "ChargeStreamer")
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        side_effect=RuntimeError("boom")
    )

    with pytest.raises(RuntimeError, match="boom"):
        await usage.recalculate(ProductSelector("PRD-1"), {})

    assert ctx.charge_filter is previous_filter
