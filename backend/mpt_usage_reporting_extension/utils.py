import datetime as dt


def to_date(parsed: dt.datetime | None) -> dt.date | None:
    """Narrow a Typer-parsed datetime option to a date, preserving None."""
    if parsed is None:
        return None
    return parsed.date()


def last_month(today: dt.date) -> dt.date:
    """A date in the previous calendar month (the latest completed month) before today."""
    return today.replace(day=1) - dt.timedelta(days=1)


def month_ordinal(year: int, month: int) -> int:
    """Map a (year, month) pair to a single comparable month ordinal."""
    return year * 12 + month  # noqa: WPS432
