import asyncio
import datetime as dt
from decimal import Decimal

import pytest
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.persistence.models import (
    PriceEstimate,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.services.estimates_uploader import (
    EstimatesUploader,
    PriceEstimateConsumer,
    PriceEstimateProducer,
    UploadOutcome,
    updatable_subscription_ids,
)
from mpt_usage_reporting_extension.types import Month


@pytest.fixture
def year():
    return 2026


@pytest.fixture
def month():
    return Month.JUNE


@pytest.fixture
def subscriptions(mocker):
    service = mocker.Mock()
    service.update = mocker.AsyncMock()
    return service


@pytest.fixture
def update(subscriptions):
    return subscriptions.update


@pytest.fixture
def price_estimate():
    amount = Decimal(1)
    return PriceEstimate(amount, amount, amount, amount)


@pytest.fixture
def calculator(mocker, price_estimate):
    estimator = mocker.AsyncMock()
    estimator.estimate.return_value = price_estimate
    return estimator


@pytest.fixture
def stored_bucket(year, month):
    return SubscriptionMonthlyAccumulation(
        subscription_id="SUB",
        agreement_id="AGR",
        year=year,
        month=month,
        ppx1=Decimal(1),
        spx1=Decimal(1),
        updated_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def subscription_repo(mocker, stored_bucket, year, month):
    window = {(year, month): stored_bucket}
    repo = mocker.AsyncMock()
    repo.get.side_effect = lambda subscription_id, year, month: window.get((year, month))
    return repo


@pytest.fixture
def updater(subscription_repo, subscriptions):
    return EstimatesUploader(subscription_repo, subscriptions)


async def _astream(*subscription_ids):
    """Yield ids from an async iterator, exercising update's async-source path."""
    await asyncio.sleep(0)
    for subscription_id in subscription_ids:
        yield subscription_id


async def test_update_uploads_each_subscription(updater, update, year, month):
    expected = {"price": {"PPxM": 1.0, "SPxM": 1.0, "PPxY": 1.0, "SPxY": 1.0}}

    await updater.update(["SUB-1", "SUB-2"], year, month)  # act

    assert {call.args[0] for call in update.call_args_list} == {"SUB-1", "SUB-2"}
    update.assert_any_call("SUB-1", expected)


async def test_update_accepts_a_lazy_iterator(updater, update, year, month):
    await updater.update(iter(["SUB-1"]), year, month)  # act

    update.assert_called_once_with(
        "SUB-1", {"price": {"PPxM": 1.0, "SPxM": 1.0, "PPxY": 1.0, "SPxY": 1.0}}
    )


async def test_update_accepts_an_async_iterator(updater, update, year, month):
    await updater.update(_astream("SUB-1"), year, month)  # act

    update.assert_called_once_with(
        "SUB-1", {"price": {"PPxM": 1.0, "SPxM": 1.0, "PPxY": 1.0, "SPxY": 1.0}}
    )


async def test_update_estimates_for_the_given_month(subscription_repo, updater, year, month):
    await updater.update(["SUB-1"], year, month)  # act

    subscription_repo.get.assert_any_await("SUB-1", year, month)


async def test_update_does_nothing_when_empty(updater, update, year, month):
    report = await updater.update([], year, month)  # act

    update.assert_not_called()
    assert not report.has_failures


async def test_update_report_flags_failure(mocker, updater, update, year, month):
    update.side_effect = [MPTError("boom"), mocker.Mock()]

    report = await updater.update(["SUB-1", "SUB-2"], year, month)  # act

    assert report.has_failures
    assert update.call_count == 2


async def test_update_report_flags_unexpected_error(mocker, updater, update, year, month):
    update.side_effect = [ValueError("boom"), mocker.Mock()]

    report = await updater.update(["SUB-1", "SUB-2"], year, month)  # act

    assert report.has_failures
    assert update.call_count == 2


def test_updatable_subscription_ids_filters(charge_accumulation_factory):
    accumulations = [
        charge_accumulation_factory("SUB-1"),
        charge_accumulation_factory("SUB-1"),
        charge_accumulation_factory("agreement_additional_AGR-9"),
        charge_accumulation_factory("SUB-2", year=None, month=None),
    ]

    result = list(updatable_subscription_ids(accumulations))  # act

    assert result == ["SUB-1"]


async def test_producer_yields_estimate_per_id(calculator, price_estimate, year, month):
    ids = ["SUB-1", "SUB-2"]
    producer = PriceEstimateProducer(calculator)

    produced = [pair async for pair in producer.produce(ids, year, month)]  # act

    assert produced == [("SUB-1", price_estimate), ("SUB-2", price_estimate)]
    calculator.estimate.assert_any_call("SUB-1", year, month)


async def test_consumer_uploads_and_returns_success(subscriptions, update, price_estimate):
    expected = {"price": {"PPxM": 1.0, "SPxM": 1.0, "PPxY": 1.0, "SPxY": 1.0}}

    outcome = await PriceEstimateConsumer(subscriptions).consume("SUB-1", price_estimate)  # act

    update.assert_awaited_once_with("SUB-1", expected)
    assert outcome == UploadOutcome("SUB-1", estimate=price_estimate)


async def test_consumer_returns_failure_on_error(subscriptions, update, price_estimate):
    update.side_effect = MPTError("boom")

    outcome = await PriceEstimateConsumer(subscriptions).consume("SUB-1", price_estimate)  # act

    assert outcome == UploadOutcome(
        "SUB-1", failed=True, exception=update.side_effect, error="boom"
    )


async def test_consumer_returns_failure_on_unexpected(subscriptions, update, price_estimate):
    update.side_effect = ValueError("boom")

    outcome = await PriceEstimateConsumer(subscriptions).consume("SUB-1", price_estimate)  # act

    assert outcome == UploadOutcome(
        "SUB-1", failed=True, exception=update.side_effect, error="boom"
    )
