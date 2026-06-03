import datetime as dt
import sqlite3
from decimal import Decimal

import pytest

from mpt_usage_reporting_extension.persistence import repositories

_YEAR = 2026
_MONTH = 5
_OTHER_MONTH = 6
_BAD_MONTH = 13
_BAD_YEAR = 99

_SUB_KEY = ("SUB-1234-5678", "AGR-1234-5678", _YEAR, _MONTH)
_AGR_KEY = ("AGR-1234-5678", _YEAR, _MONTH)

_ZERO = Decimal(0)
_PPX1 = Decimal("1543.13")
_SPX1 = Decimal("1697.45")
_FIRST = Decimal("0.1")
_SECOND = Decimal("0.2")
_TOTAL = Decimal("0.3")


def test_accumulate_inserts_new_row(subscription_repo):
    subscription_repo.accumulate(_SUB_KEY, _PPX1, _SPX1)

    stored = subscription_repo.get(_SUB_KEY)  # act

    assert stored.subscription_id == "SUB-1234-5678"
    assert stored.ppx1 == _PPX1
    assert stored.spx1 == _SPX1


def test_accumulate_is_additive_without_drift(subscription_repo):
    subscription_repo.accumulate(_SUB_KEY, _FIRST, _FIRST)
    subscription_repo.accumulate(_SUB_KEY, _SECOND, _SECOND)

    stored = subscription_repo.get(_SUB_KEY)  # act

    assert stored.ppx1 == _TOTAL
    assert stored.spx1 == _TOTAL


def test_columns_accumulate_independently(agreement_repo):
    agreement_repo.accumulate(_AGR_KEY, _FIRST, _SECOND)
    agreement_repo.accumulate(_AGR_KEY, _SECOND, _SECOND)

    stored = agreement_repo.get(_AGR_KEY)  # act

    assert stored.ppx1 == _TOTAL
    assert stored.spx1 == Decimal("0.4")


def test_get_returns_none_when_absent(subscription_repo):
    stored = subscription_repo.get(_SUB_KEY)  # act

    assert stored is None


def test_updated_at_set_on_insert(agreement_repo):
    agreement_repo.accumulate(_AGR_KEY, _FIRST, _FIRST)

    stored = agreement_repo.get(_AGR_KEY)  # act

    assert isinstance(stored.updated_at, dt.datetime)
    assert stored.updated_at.tzinfo is not None


def test_updated_at_refreshed_on_second_write(agreement_repo, mocker):
    first = "2026-05-07T08:05:00Z"
    second = "2026-05-08T09:06:00Z"
    mocker.patch.object(repositories, "utc_now_iso", side_effect=[first, second])
    agreement_repo.accumulate(_AGR_KEY, _FIRST, _FIRST)
    agreement_repo.accumulate(_AGR_KEY, _FIRST, _FIRST)

    stored = agreement_repo.get(_AGR_KEY)  # act

    assert stored.updated_at == dt.datetime.fromisoformat(second)


def test_subscription_key_distinguishes_agreement(subscription_repo):
    key_a = ("SUB-1", "AGR-1", _YEAR, _MONTH)
    key_b = ("SUB-1", "AGR-2", _YEAR, _MONTH)
    subscription_repo.accumulate(key_a, _FIRST, _ZERO)
    subscription_repo.accumulate(key_b, _SECOND, _ZERO)

    stored_a = subscription_repo.get(key_a)  # act

    assert stored_a.ppx1 == _FIRST
    assert subscription_repo.get(key_b).ppx1 == _SECOND


def test_agreement_key_distinguishes_month(agreement_repo):
    key_first = ("AGR-1", _YEAR, _MONTH)
    key_second = ("AGR-1", _YEAR, _OTHER_MONTH)
    agreement_repo.accumulate(key_first, _FIRST, _ZERO)
    agreement_repo.accumulate(key_second, _SECOND, _ZERO)

    stored_first = agreement_repo.get(key_first)  # act

    assert stored_first.ppx1 == _FIRST
    assert agreement_repo.get(key_second).ppx1 == _SECOND


def test_invalid_month_is_rejected(agreement_repo):
    with pytest.raises(sqlite3.IntegrityError):
        agreement_repo.accumulate(("AGR-1", _YEAR, _BAD_MONTH), _FIRST, _FIRST)  # act


def test_invalid_year_is_rejected(agreement_repo):
    with pytest.raises(sqlite3.IntegrityError):
        agreement_repo.accumulate(("AGR-1", _BAD_YEAR, _MONTH), _FIRST, _FIRST)  # act


def test_tables_are_independent(subscription_repo, agreement_repo):
    subscription_repo.accumulate(_SUB_KEY, _FIRST, _FIRST)

    stored = agreement_repo.get(_AGR_KEY)  # act

    assert stored is None
