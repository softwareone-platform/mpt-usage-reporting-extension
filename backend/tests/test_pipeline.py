import datetime as dt

import pytest
import typer

from mpt_usage_reporting_extension import pipeline
from mpt_usage_reporting_extension.accumulation import StatementChargeFilter
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.exceptions import ChargePriceError
from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    ProductSelector,
    SubscriptionSelector,
)
from mpt_usage_reporting_extension.services.bucket_delete import DeleteOutcome, ResolvedScope
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
    stub.resolve = mocker.AsyncMock(return_value=ResolvedScope(frozenset(), frozenset()))
    stub.resolve_agreements = mocker.AsyncMock(return_value=ResolvedScope(frozenset(), frozenset()))
    stub.delete_resolved = mocker.AsyncMock(return_value=DeleteOutcome())
    return stub


async def test_run_exits_nonzero_when_an_upload_fails(mocker, stub_database, usage, selector):
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True, failed_count=2))

    with pytest.raises(typer.Exit) as exc_info:
        await usage.run({})  # act

    assert exc_info.value.exit_code == 1


async def test_run_fails_whole_before_persisting_when_a_charge_cannot_be_priced(
    mocker, stub_database, usage, selector, notifier
):
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulator = mocker.patch.object(pipeline, "ChargeAccumulator").return_value
    accumulator.accumulate = mocker.AsyncMock(
        side_effect=ChargePriceError("Charge CHG-1 of statement SOM-1 has no BSPx1")
    )
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value

    with pytest.raises(ChargePriceError):
        await usage.run({})  # act

    # the additive run is not isolated: nothing is persisted or pushed, so the day can be re-run
    assert accumulator.accumulate.call_args.kwargs == {"isolate_agreements": False}
    persister.persist.assert_not_called()
    uploader.update.assert_not_called()
    _, error, _ = notifier.notify_failure.call_args.args
    assert error == "Charge CHG-1 of statement SOM-1 has no BSPx1"


async def test_recalculate_leaves_failed_agreements_untouched_and_rebuilds_the_rest(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    notifier,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1", "SUB-2")), agreements=frozenset(("AGR-1", "AGR-2"))
    )
    deleter.resolve_agreements.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-2",)), agreements=frozenset(("AGR-2",))
    )
    totals = charge_totals_factory(charge_accumulation_factory("SUB-1", agreement_id="AGR-1"))
    totals.failed_agreements["AGR-2"] = "Charge CHG-1 of statement SOM-1 has no BSPx1"
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulator = mocker.patch.object(pipeline, "ChargeAccumulator").return_value
    accumulator.accumulate = mocker.AsyncMock(return_value=totals)
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False, failed_count=0)
    )

    with pytest.raises(typer.Exit):
        await usage.recalculate(ProductSelector("PRD-1"), {})  # act

    assert accumulator.accumulate.call_args.kwargs == {"isolate_agreements": True}
    deleter.resolve_agreements.assert_awaited_once_with({"AGR-2"})
    # AGR-2 and its stored subscription are neither deleted nor rebuilt; AGR-1 is
    deleter.delete_resolved.assert_awaited_once_with(frozenset(("SUB-1",)), frozenset(("AGR-1",)))
    persisted = persister.persist.call_args.args[0]
    assert [bucket.subscription_id for bucket in persisted] == ["SUB-1"]
    _, error = notifier.notify_failure.call_args.args
    assert "failed_agreements=AGR-2" in error


async def test_run_arms_the_currency_guard_with_the_charged_currencies(
    mocker, stub_database, usage, selector, ctx, charge_totals_factory, charge_accumulation_factory
):
    mocker.patch.object(pipeline, "ChargeStreamer")
    totals = charge_totals_factory(
        charge_accumulation_factory("SUB-1", currencies=("USD",)),
        charge_accumulation_factory("agreement_additional_AGR-1", currencies=("USD",)),
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        return_value=totals
    )
    uploader_class = mocker.patch.object(pipeline, "EstimatesUploader")
    uploader_class.return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False, failed_count=0)
    )

    await usage.run({})  # act

    uploader_class.assert_called_once_with(
        stub_database.subscription_repository.return_value,
        ctx.api_service.subscriptions,
        dry_run=False,
    )
    update_kwargs = uploader_class.return_value.update.await_args.kwargs
    assert update_kwargs["purchase_currencies"] == {"SUB-1": frozenset(("USD",))}


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
    assert report == {
        "statements": 0,
        "accumulations": 0,
        "estimates_failed": 0,
    }
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
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(), agreements=frozenset(("AGR-1",))
    )

    await usage.recalculate(None, {})  # act

    notifier.notify_success.assert_called_once()
    command_run = notifier.notify_success.call_args.args[0]
    assert command_run.name == "recalculate"


async def test_recalculate_deletes_then_prunes(
    mocker, stub_database, usage, selector, deleter, ctx
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(), agreements=frozenset(("AGR-1",))
    )
    anchor = dt.datetime.now(tz=dt.UTC).date()  # cleanup anchors on the current UTC month

    await usage.recalculate(None, {})  # act

    # a None scope is expanded to the configured products, not a global wipe; it is resolved
    # without deleting first, then exactly that resolution is deleted once accumulation succeeded
    deleter.resolve.assert_awaited_once_with(ProductSelector("PRD-1"))
    deleter.delete_resolved.assert_awaited_once_with(frozenset(), frozenset(("AGR-1",)))
    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ("AGR-1",))
    # recalculate still prunes for retention after the re-fill
    stub_database.subscription_repository.return_value.prune.assert_awaited_once_with(
        anchor.year, Month(anchor.month)
    )
    stub_database.agreement_repository.return_value.prune.assert_awaited_once_with(
        anchor.year, Month(anchor.month)
    )


async def test_recalculate_exits_on_upload_failure(mocker, stub_database, usage, selector, deleter):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(), agreements=frozenset(("AGR-1",))
    )
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True, failed_count=1))

    with pytest.raises(typer.Exit) as exc_info:
        await usage.recalculate(None, {})  # act

    assert exc_info.value.exit_code == 1


async def test_recalculate_dry_run_runs_reads_but_skips_mutations(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    ctx,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset(("AGR-1",))
    )
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

    deleter.resolve.assert_awaited_once_with(ProductSelector("PRD-1"))
    selector.select.assert_awaited_once_with(ctx.window, ("PRD-1",), "", ("AGR-1",))
    assert subscription_repo.get.await_count > 0  # estimate calculation still performs DB reads
    subscription_repo.delete.assert_not_called()
    subscription_repo.prune.assert_not_called()
    agreement_repo = stub_database.agreement_repository.return_value
    agreement_repo.delete.assert_not_called()
    agreement_repo.prune.assert_not_called()
    subscription_api.update.assert_not_called()


async def test_recalculate_dry_run_still_processes_upload_subscription_ids(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset(("AGR-1",))
    )
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


async def test_recalculate_deletes_the_given_scope(
    mocker, stub_database, usage, selector, deleter, ctx
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset(("AGR-9",))
    )
    scope = ProductSelector("PRD-9")

    await usage.recalculate(scope, {})  # act

    deleter.resolve.assert_awaited_once_with(scope)  # resolved without deleting
    assert pipeline.BucketDeleter.call_args_list[0].kwargs == {"dry_run": True}
    deleter.delete_resolved.assert_awaited_once_with(frozenset(("SUB-1",)), frozenset(("AGR-9",)))
    assert ctx.charge_filter is None


async def test_recalculate_keeps_buckets_when_accumulation_fails(
    mocker, stub_database, usage, selector, deleter
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset()
    )
    mocker.patch.object(pipeline, "ChargeStreamer")
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        side_effect=ChargePriceError("Charge CHG-1 of statement SOM-1 has no BSPx1")
    )
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    uploader = mocker.patch.object(pipeline, "EstimatesUploader").return_value

    with pytest.raises(ChargePriceError):
        await usage.recalculate(ProductSelector("PRD-9"), {})

    # only the scope resolution ran (dry run): nothing was deleted, persisted or pushed
    deleter.delete_resolved.assert_not_called()
    persister.persist.assert_not_called()
    uploader.update.assert_not_called()


async def test_recalculate_persists_reset_only(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset(("AGR-1",))
    )
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
    # a product scope is narrowed by agreement after accumulation, not by subscription id
    assert accumulate.await_args.args[1] is None


async def test_recalculate_agreement_scope_keeps_agreement_level_and_new_subscription_charges(
    mocker,
    stub_database,
    usage,
    selector,
    deleter,
    charge_accumulation_factory,
    charge_totals_factory,
):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1", "agreement_additional_AGR-7")),
        agreements=frozenset(("AGR-7",)),
    )
    mocker.patch.object(pipeline, "ChargeStreamer")
    accumulate = mocker.AsyncMock(
        return_value=charge_totals_factory(
            charge_accumulation_factory("SUB-1", agreement_id="AGR-7"),
            charge_accumulation_factory("agreement_additional_AGR-7", agreement_id="AGR-7"),
            charge_accumulation_factory("SUB-NEW", agreement_id="AGR-7"),
        )
    )
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = accumulate
    persister = mocker.patch.object(pipeline, "AccumulationPersister").return_value
    persister.persist = mocker.AsyncMock()
    mocker.patch.object(pipeline, "EstimatesUploader").return_value.update = mocker.AsyncMock(
        return_value=mocker.Mock(has_failures=False)
    )

    await usage.recalculate(AgreementSelector("AGR-7"), {})  # act

    persisted = persister.persist.call_args.args[0]
    assert [bucket.subscription_id for bucket in persisted] == [
        "SUB-1",
        "agreement_additional_AGR-7",
        "SUB-NEW",
    ]
    assert accumulate.await_args.args[1] is None


async def test_recalculate_agreement_scope(stub_database, usage, selector, deleter, ctx):
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-7",)), agreements=frozenset(("AGR-7",))
    )

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
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset()
    )
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
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(), agreements=frozenset(("AGR-7",))
    )
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
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(), agreements=frozenset(("AGR-1", "AGR-2"))
    )
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
    # a subscription scope always covers its subscription, even with nothing stored yet
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset()
    )
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
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(), agreements=frozenset(("AGR-7",))
    )
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
    deleter.resolve.return_value = ResolvedScope(
        subscriptions=frozenset(("SUB-1",)), agreements=frozenset()
    )
    mocker.patch.object(pipeline, "ChargeStreamer")
    mocker.patch.object(pipeline, "ChargeAccumulator").return_value.accumulate = mocker.AsyncMock(
        side_effect=RuntimeError("boom")
    )

    with pytest.raises(RuntimeError, match="boom"):
        await usage.recalculate(ProductSelector("PRD-1"), {})

    assert ctx.charge_filter is previous_filter
