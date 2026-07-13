import datetime as dt
from decimal import Decimal

from mpt_usage_reporting_extension.persistence.models import SubscriptionMonthlyAccumulation
from mpt_usage_reporting_extension.persistence.postgres.repositories import engine


async def test_accumulate_inserts_new_row(
    subscription_repo, charge_factory, decimal_ppx1, decimal_spx1, sub_key
):
    await subscription_repo.accumulate(charge_factory(decimal_ppx1, decimal_spx1))

    result = await subscription_repo.get(**sub_key)

    assert result.subscription_id == sub_key["subscription_id"]
    assert result.ppx1 == decimal_ppx1
    assert result.spx1 == decimal_spx1


async def test_accumulate_is_additive_without_drift(subscription_repo, charge_factory, sub_key):
    # Sums that float gets wrong: 0.1 + 0.2 == 0.30000000000000004 and
    # 0.7 + 0.1 == 0.7999999999999999 in float; NUMERIC keeps them exact.
    await subscription_repo.accumulate(charge_factory(Decimal("0.1"), Decimal("0.7")))
    await subscription_repo.accumulate(charge_factory(Decimal("0.2"), Decimal("0.1")))

    result = await subscription_repo.get(**sub_key)

    assert result.ppx1 == Decimal("0.3")
    assert result.spx1 == Decimal("0.8")


async def test_columns_accumulate_independently(
    subscription_repo, charge_factory, decimal_first, decimal_second, sub_key, decimal_total
):
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_second))
    await subscription_repo.accumulate(charge_factory(decimal_second, decimal_second))

    result = await subscription_repo.get(**sub_key)

    assert result.ppx1 == decimal_total
    assert result.spx1 == Decimal("0.4")


async def test_get_returns_none_when_absent(subscription_repo, sub_key):
    result = await subscription_repo.get(**sub_key)

    assert result is None


async def test_updated_at_set_on_insert(subscription_repo, charge_factory, decimal_first, sub_key):
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_first))

    result = await subscription_repo.get(**sub_key)

    assert isinstance(result.updated_at, dt.datetime)
    assert result.updated_at.tzinfo is not None


async def test_updated_at_refreshed_on_second_write(
    subscription_repo, mocker, charge_factory, decimal_first, sub_key
):
    first_write = dt.datetime.fromisoformat("2026-05-07T08:05:00Z")
    second_write = dt.datetime.fromisoformat("2026-05-08T09:06:00Z")
    mocker.patch.object(engine, "utc_now", side_effect=[first_write, second_write])
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_first))
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_first))

    result = await subscription_repo.get(**sub_key)

    assert result.updated_at == second_write


async def test_key_distinguishes_month(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    subscription_id,
    year,
    month,
    other_month,
):
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_zero, month=month))
    await subscription_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, month=other_month)
    )

    result = await subscription_repo.get(subscription_id=subscription_id, year=year, month=month)

    assert result.ppx1 == decimal_first
    assert (
        await subscription_repo.get(subscription_id=subscription_id, year=year, month=other_month)
    ).ppx1 == decimal_second


async def test_updated_returns_subscription_buckets(
    subscription_repo, mocker, charge_factory, decimal_first, decimal_zero
):
    write_time = dt.datetime.fromisoformat("2026-05-07T08:05:00Z")
    mocker.patch.object(engine, "utc_now", return_value=write_time)
    charge = charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1")
    await subscription_repo.accumulate(charge)

    result = [stored async for stored in subscription_repo.updated(dt.date(2026, 5, 7))]  # act

    assert result == [
        SubscriptionMonthlyAccumulation(
            subscription_id=charge.subscription_id,
            agreement_id=charge.agreement_id,
            year=charge.year,
            month=charge.month,
            ppx1=charge.ppx1,
            spx1=charge.spx1,
            updated_at=write_time,
        )
    ]


async def test_updated_excludes_other_dates(
    subscription_repo, mocker, charge_factory, decimal_first, decimal_zero
):
    write_time = dt.datetime.fromisoformat("2026-05-07T08:05:00Z")
    mocker.patch.object(engine, "utc_now", return_value=write_time)
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_zero))

    result = [stored async for stored in subscription_repo.updated(dt.date(2026, 5, 8))]  # act

    assert result == []


async def test_subscriptions_by_agreement_one_per_id(
    subscription_repo, charge_factory, decimal_first, decimal_zero
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-2", agreement_id="AGR-2")
    )

    result = [sub async for sub in subscription_repo.subscriptions_by_agreement()]  # act

    assert result == ["SUB-1", "SUB-2"]


async def test_subscriptions_by_agreement_dedupes(
    subscription_repo, charge_factory, decimal_first, decimal_zero, month, other_month
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", month=month)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", month=other_month)
    )

    result = [sub async for sub in subscription_repo.subscriptions_by_agreement()]  # act

    assert result == ["SUB-1"]


async def test_subscriptions_by_agreement_filters(
    subscription_repo, charge_factory, decimal_first, decimal_zero
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-2", agreement_id="AGR-2")
    )

    result = [sub async for sub in subscription_repo.subscriptions_by_agreement("AGR-1")]  # act

    assert result == ["SUB-1"]


async def test_subscriptions_by_agreement_empty(subscription_repo):
    result = [sub async for sub in subscription_repo.subscriptions_by_agreement()]  # act

    assert not result


async def test_agreements_by_subscription_distinct(
    subscription_repo, charge_factory, decimal_first, decimal_zero, other_month
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(
            decimal_first,
            decimal_zero,
            subscription_id="SUB-1",
            agreement_id="AGR-1",
            month=other_month,
        )
    )

    result = [agr async for agr in subscription_repo.agreements_by_subscription("SUB-1")]  # act

    assert result == ["AGR-1"]


async def test_agreements_by_subscription_empty(subscription_repo):
    result = [agr async for agr in subscription_repo.agreements_by_subscription("SUB-9")]  # act

    assert not result


async def test_prune_drops_old_subscription_rows(
    subscription_repo, charge_factory, decimal_first, decimal_zero, subscription_id
):
    # Anchor (2026, 6): keep the 18-month window 2025-01..2026-06; drop 2024-12 and older.
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=2024, month=6)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=2024, month=12)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=2025, month=1)
    )

    result = await subscription_repo.prune(2026, 6)  # act

    assert result == 2
    assert (
        await subscription_repo.get(subscription_id=subscription_id, year=2025, month=1) is not None
    )


async def test_prune_keeps_everything_within_window(
    subscription_repo, charge_factory, decimal_first, decimal_zero
):
    # 2025-01 is the oldest month kept by the 18-month window ending (2026, 6).
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=2025, month=1)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=2026, month=6)
    )

    result = await subscription_repo.prune(2026, 6)  # act

    assert result == 0


async def test_delete_by_subscription_id(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_zero,
    subscription_id,
    year,
    month,
    other_month,
):
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_zero, month=month))
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, month=other_month)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-OTHER")
    )

    result = await subscription_repo.delete(subscription_id=subscription_id)

    assert result == 2  # every stored month of the subscription
    assert (
        await subscription_repo.get(subscription_id="SUB-OTHER", year=year, month=month) is not None
    )


async def test_delete_by_agreement(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_zero,
    year,
    month,
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-2", agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-3", agreement_id="AGR-2")
    )

    result = await subscription_repo.delete(agreement_id="AGR-1")

    assert result == 2
    assert await subscription_repo.get(subscription_id="SUB-3", year=year, month=month) is not None


async def test_delete_all_buckets(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_zero,
    year,
    month,
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-2")
    )

    result = await subscription_repo.delete()

    assert result == 2
    assert await subscription_repo.get(subscription_id="SUB-1", year=year, month=month) is None


async def test_delete_no_match(subscription_repo):
    result = await subscription_repo.delete(subscription_id="SUB-MISSING")

    assert result == 0
