"""SQLite connection and decimal adapters."""

import datetime as dt
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

from mpt_usage_reporting_extension.constants import DB_PATH

_DB_ENV_VAR = "USAGE_REPORTING_DB_PATH"
_BUSY_TIMEOUT_MS = 5000


def _adapt_decimal(amount: Decimal) -> str:
    """Serialise a Decimal to its exact string form for storage."""
    return str(amount)


def _convert_decimal(raw: bytes) -> Decimal:
    """Deserialise stored bytes back into an exact Decimal."""
    return Decimal(raw.decode("utf-8"))


def register_adapters() -> None:
    """Register Decimal <-> TEXT adapters with sqlite3 (idempotent)."""
    sqlite3.register_adapter(Decimal, _adapt_decimal)
    sqlite3.register_converter("DECIMAL", _convert_decimal)


def resolve_db_path() -> Path:
    """Resolve the storage.db path, honouring the override env var."""
    override = os.environ.get(_DB_ENV_VAR)
    return Path(override) if override else DB_PATH


def connect() -> sqlite3.Connection:
    """Open a connection with decimal converters and explicit transactions."""
    register_adapters()
    connection = sqlite3.connect(
        resolve_db_path(),
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    return connection


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with a Z suffix."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")
