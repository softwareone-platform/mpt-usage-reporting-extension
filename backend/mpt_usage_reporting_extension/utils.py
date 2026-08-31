import datetime as dt
import re

_DISALLOWED_ID_CHARS = re.compile(r"[^0-9A-Za-z-]")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")
_BACKSLASH = "\\"
_ESCAPED_BACKSLASH = r"\\"
_QUOTE = '"'
_ESCAPED_QUOTE = r"\""


def sanitize_id(raw_id: str) -> str:
    """Drop disallowed characters from an id before logging it (Sonar python:S5145).

    User-controlled ids are logged verbatim otherwise; keeping only ASCII letters, digits, and the
    ``-`` used by ids such as ``AGR-123-456`` removes the CR/LF (and other control) characters that
    would let a caller forge log lines.
    """
    return _DISALLOWED_ID_CHARS.sub("", raw_id)


def scope_label(scope: object | None) -> str:
    """Render a run's scope for a log line, carrying the id it targets, not just its type.

    Selectors are single-field frozen dataclasses, so their repr is already
    ``AgreementSelector(agreement_id='AGR-1')`` — the id a run has to be traced by.
    """
    return "all configured products" if scope is None else repr(scope)


def sanitize_log_value(raw_value: str) -> str:
    """Make an arbitrary report value safe to emit as one ``key=value`` log token.

    Unlike :func:`sanitize_id` this keeps the punctuation real values carry — the ``.`` of a
    price, the ``:`` of a timestamp — and only removes the characters that would let a value
    forge log lines (Sonar python:S5145): the ASCII control characters plus the Unicode line
    separators U+0085, U+2028 and U+2029, which a Unicode-aware log reader breaks lines on.
    Values holding whitespace of any kind - a non-breaking space included - are quoted so the
    pairs on a line stay separable — with any backslash and quote inside escaped, so a value
    cannot close its own quoting — and an empty value renders as ``-``.
    """
    cleaned = _CONTROL_CHARS.sub("", raw_value)
    if not cleaned:
        return "-"
    if not any(char.isspace() for char in cleaned):
        return cleaned
    escaped = cleaned.replace(_BACKSLASH, _ESCAPED_BACKSLASH).replace(_QUOTE, _ESCAPED_QUOTE)
    return f'"{escaped}"'


def to_date(parsed: dt.datetime | None) -> dt.date | None:
    """Narrow a Typer-parsed datetime option to a date, preserving None."""
    if parsed is None:
        return None
    return parsed.date()


def last_month(today: dt.date) -> dt.date:
    """A date in the previous calendar month (the latest completed month) before today."""
    return today.replace(day=1) - dt.timedelta(days=1)


def format_duration(delta: dt.timedelta) -> str:
    """Render a duration as ``1h 31min 16 seconds``, omitting leading zero units."""
    hours, remainder = divmod(int(delta.total_seconds()), 3600)  # noqa: WPS432
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}min")
    if seconds or not parts:
        parts.append(f"{seconds} seconds")
    return " ".join(parts)


def month_ordinal(year: int, month: int) -> int:
    """Map a (year, month) pair to a single comparable month ordinal."""
    return year * 12 + month  # noqa: WPS432
