import datetime as dt
from decimal import Decimal

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
