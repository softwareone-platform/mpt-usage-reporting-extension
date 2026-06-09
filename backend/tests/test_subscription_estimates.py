import datetime as dt
from decimal import Decimal

import pytest
import typer
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension import subscription_estimates as se
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.subscription_estimates import SubscriptionEstimatePusher
from mpt_usage_reporting_extension.window import RunWindow


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
def totals_factory(bucket_factory, charge_totals_factory):
    def factory(*subscription_ids):
        return charge_totals_factory(
            *(bucket_factory(subscription_id) for subscription_id in subscription_ids)
        )

    return factory


@pytest.fixture
def ctx_factory(mocker):
    def factory(totals):
        api_service = mocker.Mock()
        api_service.subscriptions.update = mocker.AsyncMock()
        return RunContext(
            api_service=api_service,
            window=RunWindow(
                start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
                end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
            ),
            product_ids=("PRD-1",),
            charge_totals=totals,
        )

    return factory


def _update_mock(ctx):
    return ctx.api_service.subscriptions.update


async def test_push_updates_each_real_subscription(mocker, totals_factory, ctx_factory):
    subscription_repo = mocker.Mock()
    subscription_repo.monthly_estimate.return_value = Decimal("5.00")
    subscription_repo.yearly_estimate.return_value = Decimal("5.00")
    ctx = ctx_factory(totals_factory("SUB-1", "SUB-2", "agreement_additional_AGR-9"))
    update = _update_mock(ctx)

    await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    assert {call.args[0] for call in update.call_args_list} == {"SUB-1", "SUB-2"}
    update.assert_any_call("SUB-1", {"price": {"PPxM": 5.0, "PPxY": 5.0}})


async def test_push_skips_synthetic_subscriptions(mocker, totals_factory, ctx_factory):
    subscription_repo = mocker.Mock()
    ctx = ctx_factory(totals_factory("agreement_additional_AGR-9"))
    update = _update_mock(ctx)

    await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    update.assert_not_called()


async def test_push_ignores_missing_totals(mocker, ctx_factory):
    subscription_repo = mocker.Mock()
    ctx = ctx_factory(None)
    update = _update_mock(ctx)

    await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    update.assert_not_called()


async def test_push_estimates_for_the_current_month(
    mocker, totals_factory, ctx_factory, year, month
):
    clock = mocker.patch.object(se.dt, "datetime")
    clock.now.return_value.date.return_value = dt.date(year, month, 1)
    subscription_repo = mocker.Mock()
    subscription_repo.monthly_estimate.return_value = Decimal(0)
    subscription_repo.yearly_estimate.return_value = Decimal(0)
    ctx = ctx_factory(totals_factory("SUB-1"))

    await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    subscription_repo.monthly_estimate.assert_called_once_with("SUB-1", year, month)
    subscription_repo.yearly_estimate.assert_called_once_with("SUB-1", year, month)


async def test_push_exits_nonzero_when_an_update_fails(mocker, totals_factory, ctx_factory):
    subscription_repo = mocker.Mock()
    subscription_repo.monthly_estimate.return_value = Decimal("1.00")
    subscription_repo.yearly_estimate.return_value = Decimal("1.00")
    ctx = ctx_factory(totals_factory("SUB-1", "SUB-2"))
    update = _update_mock(ctx)
    update.side_effect = [MPTError("boom"), mocker.Mock()]

    with pytest.raises(typer.Exit) as raised:
        await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    assert raised.value.exit_code == 1
    assert update.call_count == 2


async def test_push_exits_nonzero_on_unexpected_error(mocker, totals_factory, ctx_factory):
    subscription_repo = mocker.Mock()
    subscription_repo.monthly_estimate.return_value = Decimal("1.00")
    subscription_repo.yearly_estimate.return_value = Decimal("1.00")
    ctx = ctx_factory(totals_factory("SUB-1", "SUB-2"))
    update = _update_mock(ctx)
    update.side_effect = [ValueError("boom"), mocker.Mock()]

    with pytest.raises(typer.Exit) as raised:
        await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    assert raised.value.exit_code == 1
    assert update.call_count == 2


async def test_push_skips_dateless_buckets(
    mocker, charge_accumulation_factory, charge_totals_factory, ctx_factory
):
    subscription_repo = mocker.Mock()
    subscription_repo.monthly_estimate.return_value = Decimal("5.00")
    subscription_repo.yearly_estimate.return_value = Decimal("5.00")
    dateless = charge_accumulation_factory("SUB-1", year=None, month=None)
    ctx = ctx_factory(charge_totals_factory(dateless))
    update = _update_mock(ctx)

    await SubscriptionEstimatePusher().push(ctx, subscription_repo)  # act

    update.assert_not_called()
