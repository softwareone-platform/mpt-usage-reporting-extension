import datetime as dt
from decimal import Decimal

import pytest
import typer
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.persistence.models import PriceEstimate
from mpt_usage_reporting_extension.services import subscription_estimates as se
from mpt_usage_reporting_extension.services.subscription_estimates import (
    SubscriptionEstimateUpdater,
)


@pytest.fixture
def year():
    return 2026


@pytest.fixture
def month():
    return 6


@pytest.fixture
def bucket_factory(charge_accumulation_factory, year, month):
    def factory(subscription_id):
        return charge_accumulation_factory(subscription_id, year=year, month=month)

    return factory


@pytest.fixture
def accumulations_factory(bucket_factory):
    def factory(*subscription_ids):
        return [bucket_factory(subscription_id) for subscription_id in subscription_ids]

    return factory


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


async def test_push_updates_each_real_subscription(
    mocker, accumulations_factory, subscriptions, update
):
    subscription_repo = mocker.AsyncMock()
    subscription_repo.estimate.return_value = PriceEstimate(
        Decimal(5), Decimal(6), Decimal(50), Decimal(60)
    )
    accumulations = accumulations_factory("SUB-1", "SUB-2", "agreement_additional_AGR-9")

    await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(accumulations)  # act

    assert {call.args[0] for call in update.call_args_list} == {"SUB-1", "SUB-2"}
    update.assert_any_call(
        "SUB-1", {"price": {"PPxM": 5.0, "SPxM": 6.0, "PPxY": 50.0, "SPxY": 60.0}}
    )


async def test_push_skips_synthetic_subscriptions(
    mocker, accumulations_factory, subscriptions, update
):
    subscription_repo = mocker.AsyncMock()
    accumulations = accumulations_factory("agreement_additional_AGR-9")

    await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(accumulations)  # act

    update.assert_not_called()


async def test_push_does_nothing_when_empty(mocker, subscriptions, update):
    subscription_repo = mocker.AsyncMock()

    await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update([])  # act

    update.assert_not_called()


async def test_push_estimates_for_the_current_month(
    mocker, accumulations_factory, subscriptions, price_estimate, year, month
):
    clock = mocker.patch.object(se.dt, "datetime")
    clock.now.return_value.date.return_value = dt.date(year, month, 1)
    subscription_repo = mocker.AsyncMock()
    subscription_repo.estimate.return_value = price_estimate
    accumulations = accumulations_factory("SUB-1")

    await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(accumulations)  # act

    subscription_repo.estimate.assert_called_once_with("SUB-1", year, month)


async def test_push_exits_nonzero_when_an_update_fails(
    mocker, accumulations_factory, subscriptions, update, price_estimate
):
    subscription_repo = mocker.AsyncMock()
    subscription_repo.estimate.return_value = price_estimate
    accumulations = accumulations_factory("SUB-1", "SUB-2")
    update.side_effect = [MPTError("boom"), mocker.Mock()]

    with pytest.raises(typer.Exit) as raised:
        await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(
            accumulations
        )  # act

    assert raised.value.exit_code == 1
    assert update.call_count == 2


async def test_push_exits_nonzero_on_unexpected_error(
    mocker, accumulations_factory, subscriptions, update, price_estimate
):
    subscription_repo = mocker.AsyncMock()
    subscription_repo.estimate.return_value = price_estimate
    accumulations = accumulations_factory("SUB-1", "SUB-2")
    update.side_effect = [ValueError("boom"), mocker.Mock()]

    with pytest.raises(typer.Exit) as raised:
        await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(
            accumulations
        )  # act

    assert raised.value.exit_code == 1
    assert update.call_count == 2


async def test_push_skips_dateless_buckets(
    mocker, charge_accumulation_factory, subscriptions, update, price_estimate
):
    subscription_repo = mocker.AsyncMock()
    subscription_repo.estimate.return_value = price_estimate
    dateless = charge_accumulation_factory("SUB-1", year=None, month=None)

    await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update([dateless])  # act

    update.assert_not_called()
