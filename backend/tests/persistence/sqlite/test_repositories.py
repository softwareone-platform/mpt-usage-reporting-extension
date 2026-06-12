import datetime as dt
import sqlite3
from decimal import Decimal
from types import MappingProxyType

import pytest

from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.sqlite import repositories


@pytest.fixture
def year():
    return 2026


@pytest.fixture
def prev_year():
    return 2025


@pytest.fixture
def month():
    return 5


@pytest.fixture
def other_month():
    return 6


@pytest.fixture
def last_month():
    return 12


@pytest.fixture
def first_month():
    return 1


@pytest.fixture
def bad_month():
    return 13


@pytest.fixture
def bad_year():
    return 99


@pytest.fixture
def subscription_id():
    return "SUB-1234-5678"


@pytest.fixture
def agreement_id():
    return "AGR-1234-5678"


@pytest.fixture
def decimal_zero():
    return Decimal(0)


@pytest.fixture
def decimal_ppx1():
    return Decimal("1543.13")


@pytest.fixture
def decimal_spx1():
    return Decimal("1697.45")


@pytest.fixture
def decimal_first():
    return Decimal("0.1")


@pytest.fixture
def decimal_second():
    return Decimal("0.2")


@pytest.fixture
def decimal_total():
    return Decimal("0.3")


@pytest.fixture
def sub_key(subscription_id, agreement_id, year, month):
    return MappingProxyType({
        "subscription_id": subscription_id,
        "agreement_id": agreement_id,
        "year": year,
        "month": month,
    })


@pytest.fixture
def agr_key(agreement_id, year, month):
    return MappingProxyType({"agreement_id": agreement_id, "year": year, "month": month})


@pytest.fixture
def charge_factory(subscription_id, agreement_id, year, month):
    def factory(ppx1, spx1, **overrides):
        fields: dict[str, object] = {
            "subscription_id": subscription_id,
            "agreement_id": agreement_id,
            "year": year,
            "month": month,
            "ppx1": ppx1,
            "spx1": spx1,
        }
        fields.update(overrides)
        return Charge(**fields)  # type: ignore[arg-type]

    return factory


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
    # 0.7 + 0.1 == 0.7999999999999999 in float; decimal_add keeps them exact.
    await subscription_repo.accumulate(charge_factory(Decimal("0.1"), Decimal("0.7")))
    await subscription_repo.accumulate(charge_factory(Decimal("0.2"), Decimal("0.1")))

    result = await subscription_repo.get(**sub_key)

    assert result.ppx1 == Decimal("0.3")
    assert result.spx1 == Decimal("0.8")


async def test_columns_accumulate_independently(
    agreement_repo, charge_factory, decimal_first, decimal_second, agr_key, decimal_total
):
    await agreement_repo.accumulate(charge_factory(decimal_first, decimal_second))
    await agreement_repo.accumulate(charge_factory(decimal_second, decimal_second))

    result = await agreement_repo.get(**agr_key)

    assert result.ppx1 == decimal_total
    assert result.spx1 == Decimal("0.4")


async def test_get_returns_none_when_absent(subscription_repo, sub_key):
    result = await subscription_repo.get(**sub_key)

    assert result is None


async def test_updated_at_set_on_insert(agreement_repo, charge_factory, decimal_first, agr_key):
    await agreement_repo.accumulate(charge_factory(decimal_first, decimal_first))

    result = await agreement_repo.get(**agr_key)

    assert isinstance(result.updated_at, dt.datetime)
    assert result.updated_at.tzinfo is not None


async def test_updated_at_refreshed_on_second_write(
    agreement_repo, mocker, charge_factory, decimal_first, agr_key
):
    first_write = "2026-05-07T08:05:00Z"
    second_write = "2026-05-08T09:06:00Z"
    mocker.patch.object(repositories, "utc_now_iso", side_effect=[first_write, second_write])
    await agreement_repo.accumulate(charge_factory(decimal_first, decimal_first))
    await agreement_repo.accumulate(charge_factory(decimal_first, decimal_first))

    result = await agreement_repo.get(**agr_key)

    assert result.updated_at == dt.datetime.fromisoformat(second_write)


async def test_subscription_key_distinguishes_agreement(
    subscription_repo, charge_factory, decimal_first, decimal_second, decimal_zero, year, month
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1", agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, subscription_id="SUB-1", agreement_id="AGR-2")
    )

    result = await subscription_repo.get(
        subscription_id="SUB-1",
        agreement_id="AGR-1",
        year=year,
        month=month,
    )

    assert result.ppx1 == decimal_first
    assert (
        await subscription_repo.get(
            subscription_id="SUB-1",
            agreement_id="AGR-2",
            year=year,
            month=month,
        )
    ).ppx1 == decimal_second


async def test_agreement_key_distinguishes_month(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    year,
    month,
    other_month,
):
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1", month=month)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, agreement_id="AGR-1", month=other_month)
    )

    result = await agreement_repo.get(agreement_id="AGR-1", year=year, month=month)

    assert result.ppx1 == decimal_first
    assert (
        await agreement_repo.get(agreement_id="AGR-1", year=year, month=other_month)
    ).ppx1 == decimal_second


async def test_invalid_month_is_rejected(agreement_repo, charge_factory, decimal_first, bad_month):
    with pytest.raises(sqlite3.IntegrityError):
        await agreement_repo.accumulate(
            charge_factory(decimal_first, decimal_first, agreement_id="AGR-1", month=bad_month)
        )  # act


async def test_invalid_year_is_rejected(agreement_repo, charge_factory, decimal_first, bad_year):
    with pytest.raises(sqlite3.IntegrityError):
        await agreement_repo.accumulate(
            charge_factory(decimal_first, decimal_first, agreement_id="AGR-1", year=bad_year)
        )  # act


async def test_tables_are_independent(
    subscription_repo, agreement_repo, charge_factory, decimal_first, agr_key
):
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_first))

    result = await agreement_repo.get(**agr_key)

    assert result is None


def test_utc_now_iso_is_z_suffixed_utc():
    result = repositories.utc_now_iso()

    assert result.endswith("Z")
    parsed = dt.datetime.fromisoformat(result)
    assert parsed.utcoffset() == dt.timedelta(0)


async def test_monthly_estimate_sums_the_month(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    subscription_id,
    year,
    month,
    decimal_total,
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, agreement_id="AGR-1")
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, agreement_id="AGR-2")
    )

    result = await subscription_repo.monthly_estimate(subscription_id, year, month)  # act

    assert result == decimal_total


async def test_monthly_estimate_ignores_other_months(
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

    result = await subscription_repo.monthly_estimate(subscription_id, year, other_month)  # act

    assert result == decimal_second


async def test_monthly_estimate_is_zero_when_absent(subscription_repo, year, month, decimal_zero):
    result = await subscription_repo.monthly_estimate("SUB-MISSING", year, month)  # act

    assert result == decimal_zero


async def test_yearly_estimate_spans_a_year_boundary(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    subscription_id,
    year,
    prev_year,
    last_month,
    first_month,
    decimal_total,
):
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=prev_year, month=last_month)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, year=year, month=first_month)
    )

    result = await subscription_repo.yearly_estimate(subscription_id, year, first_month)  # act

    assert result == decimal_total


async def test_yearly_estimate_excludes_old_months(
    subscription_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    subscription_id,
    year,
    prev_year,
    month,
    other_month,
):
    # Trailing 12 months ending (2026, 5): (2025, 6) is the oldest included month, (2025, 5) is out.
    await subscription_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=prev_year, month=other_month)
    )
    await subscription_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, year=prev_year, month=month)
    )

    result = await subscription_repo.yearly_estimate(subscription_id, year, month)  # act

    assert result == decimal_first


async def test_agreement_monthly_estimate_sums(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    agreement_id,
    year,
    month,
    decimal_total,
):
    await agreement_repo.accumulate(charge_factory(decimal_first, decimal_zero))
    await agreement_repo.accumulate(charge_factory(decimal_second, decimal_zero))

    result = await agreement_repo.monthly_estimate(agreement_id, year, month)  # act

    assert result == decimal_total


async def test_agreement_monthly_estimate_absent(agreement_repo, year, month, decimal_zero):
    result = await agreement_repo.monthly_estimate("AGR-MISSING", year, month)  # act

    assert result == decimal_zero


async def test_agreement_yearly_estimate_spans_year(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    agreement_id,
    year,
    prev_year,
    last_month,
    first_month,
    decimal_total,
):
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=prev_year, month=last_month)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, year=year, month=first_month)
    )

    result = await agreement_repo.yearly_estimate(agreement_id, year, first_month)  # act

    assert result == decimal_total


async def test_agreement_yearly_estimate_window(
    agreement_repo,
    charge_factory,
    decimal_first,
    decimal_second,
    decimal_zero,
    agreement_id,
    year,
    prev_year,
    month,
    other_month,
):
    await agreement_repo.accumulate(
        charge_factory(decimal_first, decimal_zero, year=prev_year, month=other_month)
    )
    await agreement_repo.accumulate(
        charge_factory(decimal_second, decimal_zero, year=prev_year, month=month)
    )

    result = await agreement_repo.yearly_estimate(agreement_id, year, month)  # act

    assert result == decimal_first


async def test_updated_returns_subscription_charges(
    subscription_repo, mocker, charge_factory, decimal_first, decimal_zero
):
    mocker.patch.object(repositories, "utc_now_iso", return_value="2026-05-07T08:05:00Z")
    charge = charge_factory(decimal_first, decimal_zero, subscription_id="SUB-1")
    await subscription_repo.accumulate(charge)

    result = [stored async for stored in subscription_repo.updated(dt.date(2026, 5, 7))]  # act

    assert result == [charge]


async def test_updated_excludes_other_dates(
    subscription_repo, mocker, charge_factory, decimal_first, decimal_zero
):
    mocker.patch.object(repositories, "utc_now_iso", return_value="2026-05-07T08:05:00Z")
    await subscription_repo.accumulate(charge_factory(decimal_first, decimal_zero))

    result = [stored async for stored in subscription_repo.updated(dt.date(2026, 5, 8))]  # act

    assert result == []


async def test_agreement_updated_returns_charges(
    agreement_repo, mocker, charge_factory, decimal_first, decimal_zero, agreement_id, year, month
):
    mocker.patch.object(repositories, "utc_now_iso", return_value="2026-05-07T08:05:00Z")
    await agreement_repo.accumulate(charge_factory(decimal_first, decimal_zero))

    result = [stored async for stored in agreement_repo.updated(dt.date(2026, 5, 7))]  # act

    assert result == [Charge("", agreement_id, year, month, decimal_first, decimal_zero)]
