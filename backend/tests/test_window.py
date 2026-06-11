import datetime as dt

import pytest
import typer

from mpt_usage_reporting_extension.window import RunWindow, resolve_window


def _utc(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{iso}T00:00:00+00:00")


def test_default_window_is_yesterday_utc():
    today = dt.date.fromisoformat("2026-06-02")

    result = resolve_window(today=today)

    assert result == RunWindow(start=_utc("2026-06-01"), end=_utc("2026-06-02"))


def test_date_selects_a_single_day():
    result = resolve_window(date=dt.date.fromisoformat("2026-05-10"))

    assert result == RunWindow(start=_utc("2026-05-10"), end=_utc("2026-05-11"))


def test_from_till_range_is_half_open():
    result = resolve_window(
        from_date=dt.date.fromisoformat("2026-05-01"),
        till_date=dt.date.fromisoformat("2026-05-03"),
    )

    assert result == RunWindow(start=_utc("2026-05-01"), end=_utc("2026-05-04"))


def test_single_bound_applies_to_both_ends():
    result = resolve_window(from_date=dt.date.fromisoformat("2026-05-07"))

    assert result == RunWindow(start=_utc("2026-05-07"), end=_utc("2026-05-08"))


def test_date_combined_with_range_is_rejected():
    with pytest.raises(typer.BadParameter):
        resolve_window(
            date=dt.date.fromisoformat("2026-05-01"),
            till_date=dt.date.fromisoformat("2026-05-02"),
        )


def test_from_after_till_is_rejected():
    with pytest.raises(typer.BadParameter):
        resolve_window(
            from_date=dt.date.fromisoformat("2026-05-05"),
            till_date=dt.date.fromisoformat("2026-05-01"),
        )
