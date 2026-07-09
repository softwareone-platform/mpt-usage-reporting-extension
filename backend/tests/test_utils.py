import datetime as dt

# `utils` is a wemake-blacklisted module name (WPS100); importing from it raises WPS347.
from mpt_usage_reporting_extension.utils import (  # noqa: WPS347
    format_duration,
    last_month,
    sanitize_id,
    to_date,
)


def test_sanitize_id_keeps_alphanumerics_and_hyphens():
    result = sanitize_id("AGR-123-456")

    assert result == "AGR-123-456"


def test_sanitize_id_strips_other_punctuation():
    result = sanitize_id("AGR_123.456")

    assert result == "AGR123456"


def test_sanitize_id_removes_newline_forgery():
    result = sanitize_id("AGR-1\n2026 INFO forged entry")

    assert result == "AGR-12026INFOforgedentry"


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


def test_format_duration_full():
    result = format_duration(dt.timedelta(hours=1, minutes=31, seconds=16))  # act

    assert result == "1h 31min 16 seconds"


def test_format_duration_minutes_and_seconds():
    result = format_duration(dt.timedelta(minutes=5, seconds=3))  # act

    assert result == "5min 3 seconds"


def test_format_duration_seconds_only():
    result = format_duration(dt.timedelta(seconds=42))  # act

    assert result == "42 seconds"


def test_format_duration_omits_zero_trailing_seconds():
    result = format_duration(dt.timedelta(hours=2))  # act

    assert result == "2h"


def test_format_duration_zero():
    result = format_duration(dt.timedelta())  # act

    assert result == "0 seconds"
