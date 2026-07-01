import datetime as dt

# `utils` is a wemake-blacklisted module name (WPS100); importing from it raises WPS347.
from mpt_usage_reporting_extension.utils import (  # noqa: WPS347
    last_month,
    sanitize_id,
    to_date,
)


def test_sanitize_id_keeps_alphanumerics():
    result = sanitize_id("AGR1234")

    assert result == "AGR1234"


def test_sanitize_id_strips_non_alphanumerics():
    result = sanitize_id("AGR-1234")

    assert result == "AGR1234"


def test_sanitize_id_removes_newline_forgery():
    result = sanitize_id("AGR-1\n2026 INFO forged entry")

    assert result == "AGR12026INFOforgedentry"


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
