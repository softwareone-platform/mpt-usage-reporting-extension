"""SQLite connection, decimal adapters and schema creation."""

import datetime as dt
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

_DB_ENV_VAR = "USAGE_REPORTING_DB_PATH"
_DEFAULT_DB_PATH = Path(__file__).parents[2] / "storage.db"
_BUSY_TIMEOUT_MS = 5000

_CREATE_SUBSCRIPTION_TABLE = """
CREATE TABLE IF NOT EXISTS subscription_monthly_accumulation (
    subscription_id TEXT NOT NULL,
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 DECIMAL NOT NULL DEFAULT '0',
    spx1 DECIMAL NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, agreement_id, year, month)
)
"""

_CREATE_AGREEMENT_TABLE = """
CREATE TABLE IF NOT EXISTS agreement_monthly_accumulation (
    agreement_id TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    ppx1 DECIMAL NOT NULL DEFAULT '0',
    spx1 DECIMAL NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agreement_id, year, month)
)
"""


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
    return Path(override) if override else _DEFAULT_DB_PATH


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


def create_schema(connection: sqlite3.Connection) -> None:
    """Create both accumulation tables if they do not already exist."""
    connection.execute(_CREATE_SUBSCRIPTION_TABLE)
    connection.execute(_CREATE_AGREEMENT_TABLE)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO8601 string with a Z suffix."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")
