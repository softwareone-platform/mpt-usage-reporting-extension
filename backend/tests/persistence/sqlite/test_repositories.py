import datetime as dt
import sqlite3
from decimal import Decimal
from types import MappingProxyType

import pytest

from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.sqlite import repositories

_MONTH = 5
_OTHER_MONTH = 6
_BAD_MONTH = 13
_BAD2026 = 99

_SUBSCRIPTION_ID = "SUB-1234-5678"
_AGREEMENT_ID = "AGR-1234-5678"

_SUB_KEY = MappingProxyType({
    "subscription_id": _SUBSCRIPTION_ID,
    "agreement_id": _AGREEMENT_ID,
    "year": 2026,
    "month": _MONTH,
})
_AGR_KEY = MappingProxyType({"agreement_id": _AGREEMENT_ID, "year": 2026, "month": _MONTH})

_ZERO = Decimal(0)
_PPX1 = Decimal("1543.13")
_SPX1 = Decimal("1697.45")
_FIRST = Decimal("0.1")
_SECOND = Decimal("0.2")
_TOTAL = Decimal("0.3")


def charge_factory(ppx1: Decimal, spx1: Decimal, **overrides: object) -> Charge:
    fields: dict[str, object] = {
        "subscription_id": _SUBSCRIPTION_ID,
        "agreement_id": _AGREEMENT_ID,
        "year": 2026,
        "month": _MONTH,
        "ppx1": ppx1,
        "spx1": spx1,
    }
    fields.update(overrides)
    return Charge(**fields)  # type: ignore[arg-type]


def test_accumulate_inserts_new_row(subscription_repo):
    subscription_repo.accumulate(charge_factory(_PPX1, _SPX1))

    result = subscription_repo.get(**_SUB_KEY)

    assert result.subscription_id == _SUBSCRIPTION_ID
    assert result.ppx1 == _PPX1
    assert result.spx1 == _SPX1


def test_accumulate_is_additive_without_drift(subscription_repo):
    # Sums that float gets wrong: 0.1 + 0.2 == 0.30000000000000004 and
    # 0.7 + 0.1 == 0.7999999999999999 in float; decimal_add keeps them exact.
    subscription_repo.accumulate(charge_factory(Decimal("0.1"), Decimal("0.7")))
    subscription_repo.accumulate(charge_factory(Decimal("0.2"), Decimal("0.1")))

    result = subscription_repo.get(**_SUB_KEY)

    assert result.ppx1 == Decimal("0.3")
    assert result.spx1 == Decimal("0.8")


def test_columns_accumulate_independently(agreement_repo):
    agreement_repo.accumulate(charge_factory(_FIRST, _SECOND))
    agreement_repo.accumulate(charge_factory(_SECOND, _SECOND))

    result = agreement_repo.get(**_AGR_KEY)

    assert result.ppx1 == _TOTAL
    assert result.spx1 == Decimal("0.4")


def test_get_returns_none_when_absent(subscription_repo):
    result = subscription_repo.get(**_SUB_KEY)

    assert result is None


def test_updated_at_set_on_insert(agreement_repo):
    agreement_repo.accumulate(charge_factory(_FIRST, _FIRST))

    result = agreement_repo.get(**_AGR_KEY)

    assert isinstance(result.updated_at, dt.datetime)
    assert result.updated_at.tzinfo is not None


def test_updated_at_refreshed_on_second_write(agreement_repo, mocker):
    first = "2026-05-07T08:05:00Z"
    second = "2026-05-08T09:06:00Z"
    mocker.patch.object(repositories, "utc_now_iso", side_effect=[first, second])
    agreement_repo.accumulate(charge_factory(_FIRST, _FIRST))
    agreement_repo.accumulate(charge_factory(_FIRST, _FIRST))

    result = agreement_repo.get(**_AGR_KEY)

    assert result.updated_at == dt.datetime.fromisoformat(second)


def test_subscription_key_distinguishes_agreement(subscription_repo):
    subscription_repo.accumulate(
        charge_factory(_FIRST, _ZERO, subscription_id="SUB-1", agreement_id="AGR-1")
    )
    subscription_repo.accumulate(
        charge_factory(_SECOND, _ZERO, subscription_id="SUB-1", agreement_id="AGR-2")
    )

    result = subscription_repo.get(
        subscription_id="SUB-1",
        agreement_id="AGR-1",
        year=2026,
        month=_MONTH,
    )

    assert result.ppx1 == _FIRST
    assert (
        subscription_repo.get(
            subscription_id="SUB-1",
            agreement_id="AGR-2",
            year=2026,
            month=_MONTH,
        ).ppx1
        == _SECOND
    )


def test_agreement_key_distinguishes_month(agreement_repo):
    agreement_repo.accumulate(charge_factory(_FIRST, _ZERO, agreement_id="AGR-1", month=_MONTH))
    agreement_repo.accumulate(
        charge_factory(_SECOND, _ZERO, agreement_id="AGR-1", month=_OTHER_MONTH)
    )

    result = agreement_repo.get(agreement_id="AGR-1", year=2026, month=_MONTH)

    assert result.ppx1 == _FIRST
    assert agreement_repo.get(agreement_id="AGR-1", year=2026, month=_OTHER_MONTH).ppx1 == _SECOND


def test_invalid_month_is_rejected(agreement_repo):
    with pytest.raises(sqlite3.IntegrityError):
        agreement_repo.accumulate(
            charge_factory(_FIRST, _FIRST, agreement_id="AGR-1", month=_BAD_MONTH)
        )  # act


def test_invalid_year_is_rejected(agreement_repo):
    with pytest.raises(sqlite3.IntegrityError):
        agreement_repo.accumulate(
            charge_factory(_FIRST, _FIRST, agreement_id="AGR-1", year=_BAD2026)
        )  # act


def test_tables_are_independent(subscription_repo, agreement_repo):
    subscription_repo.accumulate(charge_factory(_FIRST, _FIRST))

    result = agreement_repo.get(**_AGR_KEY)

    assert result is None


def test_utc_now_iso_is_z_suffixed_utc():
    result = repositories.utc_now_iso()

    assert result.endswith("Z")
    parsed = dt.datetime.fromisoformat(result)
    assert parsed.utcoffset() == dt.timedelta(0)
