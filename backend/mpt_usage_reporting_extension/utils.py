import datetime as dt
import re

_DISALLOWED_ID_CHARS = re.compile(r"[^0-9A-Za-z-]")


def sanitize_id(raw_id: str) -> str:
    """Drop disallowed characters from an id before logging it (Sonar python:S5145).

    User-controlled ids are logged verbatim otherwise; keeping only ASCII letters, digits, and the
    ``-`` used by ids such as ``AGR-123-456`` removes the CR/LF (and other control) characters that
    would let a caller forge log lines.
    """
    return _DISALLOWED_ID_CHARS.sub("", raw_id)


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
