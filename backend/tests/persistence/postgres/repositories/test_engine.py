import datetime as dt
from decimal import Decimal
from types import MappingProxyType

import pytest

from mpt_usage_reporting_extension.persistence.postgres.repositories import engine


@pytest.fixture
def accumulation_engine(db):
    return engine.AccumulationEngine(
        connection=db.connection, table="subscription_monthly_accumulation"
    )


@pytest.fixture
def key_fields():
    return MappingProxyType({
        "subscription_id": "SUB-1",
        "agreement_id": "AGR-1",
        "year": 2026,
        "month": 5,
    })


@pytest.fixture
def decimal_zero():
    return Decimal(0)


@pytest.fixture
def decimal_first():
    return Decimal("0.1")


@pytest.fixture
def decimal_second():
    return Decimal("0.2")


def test_utc_now_is_aware_utc_without_microseconds():
    result = engine.utc_now()

    assert result.utcoffset() == dt.timedelta(0)
    assert result.microsecond == 0


async def test_accumulate_inserts_row(accumulation_engine, key_fields):
    ppx1 = Decimal("1543.13")
    spx1 = Decimal("1697.45")
    await accumulation_engine.accumulate(ppx1=ppx1, spx1=spx1, **key_fields)

    result = await accumulation_engine.get(**key_fields)

    assert result["subscription_id"] == key_fields["subscription_id"]
    assert result["ppx1"] == ppx1
    assert result["spx1"] == spx1


async def test_accumulate_is_additive_without_drift(
    accumulation_engine, key_fields, decimal_first, decimal_second
):
    # Sums that float gets wrong: 0.1 + 0.2 == 0.30000000000000004 and
    # 0.7 + 0.1 == 0.7999999999999999 in float; NUMERIC keeps them exact.
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=Decimal("0.7"), **key_fields)
    await accumulation_engine.accumulate(ppx1=decimal_second, spx1=decimal_first, **key_fields)

    result = await accumulation_engine.get(**key_fields)

    assert result["ppx1"] == Decimal("0.3")
    assert result["spx1"] == Decimal("0.8")


async def test_get_returns_none_when_absent(accumulation_engine, key_fields):
    result = await accumulation_engine.get(**key_fields)

    assert result is None


async def test_accumulate_stamps_updated_at_from_utc_now(
    accumulation_engine, mocker, key_fields, decimal_first, decimal_zero
):
    write_moment = dt.datetime.fromisoformat("2026-05-07T08:05:00Z")
    mocker.patch.object(engine, "utc_now", return_value=write_moment)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)

    result = await accumulation_engine.get(**key_fields)

    assert result["updated_at"] == write_moment


async def test_delete_before_drops_older_month_ordinals(
    accumulation_engine, key_fields, decimal_first, decimal_zero
):
    old_bucket = {**key_fields, "year": 2024, "month": 12}
    kept_bucket = {**key_fields, "year": 2025, "month": 1}
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **old_bucket)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **kept_bucket)

    result = await accumulation_engine.delete_before(2025 * 12 + 1)  # act

    assert result == 1
    assert await accumulation_engine.get(**kept_bucket) is not None


async def test_delete_matches_equals_filter(
    accumulation_engine, key_fields, decimal_first, decimal_zero
):
    other_bucket = {**key_fields, "subscription_id": "SUB-2"}
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **other_bucket)

    result = await accumulation_engine.delete(subscription_id="SUB-2")

    assert result == 1
    assert await accumulation_engine.get(**key_fields) is not None


async def test_delete_without_filter_deletes_every_row(
    accumulation_engine, key_fields, decimal_first, decimal_zero
):
    other_bucket = {**key_fields, "subscription_id": "SUB-2"}
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **other_bucket)

    result = await accumulation_engine.delete()

    assert result == 2
    assert await accumulation_engine.get(**key_fields) is None


async def test_delete_returns_zero_when_nothing_matches(accumulation_engine):
    result = await accumulation_engine.delete(subscription_id="SUB-MISSING")

    assert result == 0


async def test_rows_updated_on_yields_rows_of_the_utc_day(
    accumulation_engine, mocker, key_fields, decimal_first, decimal_zero
):
    write_moment = dt.datetime.fromisoformat("2026-05-07T08:05:00Z")
    mocker.patch.object(engine, "utc_now", return_value=write_moment)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)

    result = [row async for row in accumulation_engine.rows_updated_on("2026-05-07")]  # act

    assert [row["subscription_id"] for row in result] == [key_fields["subscription_id"]]


async def test_rows_updated_on_excludes_other_days(
    accumulation_engine, mocker, key_fields, decimal_first, decimal_zero
):
    write_moment = dt.datetime.fromisoformat("2026-05-07T08:05:00Z")
    mocker.patch.object(engine, "utc_now", return_value=write_moment)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)

    result = [row async for row in accumulation_engine.rows_updated_on("2026-05-08")]  # act

    assert result == []


async def test_distinct_yields_ordered_deduplicated_values(
    accumulation_engine, key_fields, decimal_first, decimal_zero
):
    other_subscription = {**key_fields, "subscription_id": "SUB-2"}
    other_month = {**key_fields, "month": 6}
    await accumulation_engine.accumulate(
        ppx1=decimal_first, spx1=decimal_zero, **other_subscription
    )
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **other_month)

    result = [sub async for sub in accumulation_engine.distinct("subscription_id")]  # act

    assert result == ["SUB-1", "SUB-2"]


async def test_overlapping_same_operation_streams_both_complete(
    accumulation_engine, key_fields, decimal_first, decimal_zero
):
    other_subscription = {**key_fields, "subscription_id": "SUB-2"}
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)
    await accumulation_engine.accumulate(
        ppx1=decimal_first, spx1=decimal_zero, **other_subscription
    )
    first = accumulation_engine.distinct("subscription_id")
    second = accumulation_engine.distinct("subscription_id")

    result = [await anext(first), await anext(second)]  # act
    result += [sub async for sub in first] + [sub async for sub in second]

    assert result == ["SUB-1", "SUB-1", "SUB-2", "SUB-2"]


async def test_distinct_filters_by_equals(
    accumulation_engine, key_fields, decimal_first, decimal_zero
):
    other_agreement = {**key_fields, "subscription_id": "SUB-2", "agreement_id": "AGR-2"}
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **key_fields)
    await accumulation_engine.accumulate(ppx1=decimal_first, spx1=decimal_zero, **other_agreement)

    result = [
        sub async for sub in accumulation_engine.distinct("subscription_id", agreement_id="AGR-1")
    ]  # act

    assert result == ["SUB-1"]
