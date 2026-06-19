import datetime as dt

# `utils` is a wemake-blacklisted module name (WPS100); importing from it raises WPS347.
from mpt_usage_reporting_extension.utils import last_month, to_date  # noqa: WPS347


def test_to_date_narrows_datetime():
    moment = dt.datetime(2026, 6, 19, tzinfo=dt.UTC)

    result = to_date(moment)  # act

    assert result == dt.date(2026, 6, 19)


def test_to_date_preserves_none():
    result = to_date(None)  # act

    assert result is None


def test_last_month_mid_month():
    result = last_month(dt.date(2026, 6, 19))  # act

    assert (result.year, result.month) == (2026, 5)


def test_last_month_rolls_over_year():
    result = last_month(dt.date(2026, 1, 10))  # act

    assert (result.year, result.month) == (2025, 12)
