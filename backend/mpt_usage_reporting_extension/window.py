"""Run-window resolution for the usage reporting CLI."""

import datetime as dt
from dataclasses import dataclass
from typing import cast

import typer


@dataclass(frozen=True)
class RunWindow:
    """A half-open UTC time window ``[start, end)``."""

    start: dt.datetime
    end: dt.datetime


def resolve_window(
    date: dt.date | None = None,
    from_date: dt.date | None = None,
    till_date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> RunWindow:
    """Resolve the run window from the CLI date options.

    Defaults to yesterday (UTC). ``date`` selects a single UTC day; ``from_date`` and
    ``till_date`` select an inclusive day range (a single provided bound applies to both).
    The resulting window is half-open: ``start`` at 00:00 UTC inclusive, ``end`` at the
    following day's 00:00 UTC exclusive.

    Raises:
        typer.BadParameter: If ``date`` is combined with a range, or ``from_date`` is
            after ``till_date``.
    """
    if date is not None and (from_date is not None or till_date is not None):
        raise typer.BadParameter("--date cannot be combined with --from-date/--till-date")

    today = today or dt.datetime.now(dt.UTC).date()
    start_day, end_day = _resolve_days(date, from_date, till_date, today)
    if start_day > end_day:
        raise typer.BadParameter("--from-date must not be after --till-date")

    next_day = end_day + dt.timedelta(days=1)
    return RunWindow(start=_start_of_day(start_day), end=_start_of_day(next_day))


def _start_of_day(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)


def _resolve_days(
    date: dt.date | None,
    from_date: dt.date | None,
    till_date: dt.date | None,
    today: dt.date,
) -> tuple[dt.date, dt.date]:
    if date is not None:
        return date, date
    if from_date is None and till_date is None:
        yesterday = today - dt.timedelta(days=1)
        return yesterday, yesterday
    start_day = from_date or till_date
    end_day = till_date or from_date
    return cast("dt.date", start_day), cast("dt.date", end_day)
