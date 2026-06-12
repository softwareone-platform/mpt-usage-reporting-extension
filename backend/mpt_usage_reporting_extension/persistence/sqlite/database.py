import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Self

import aiosqlite

from mpt_usage_reporting_extension.constants import DEFAULT_DB_PATH
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.persistence.sqlite import repositories

_BUSY_TIMEOUT_MS = 5000


def _adapt_decimal(amount: Decimal) -> str:
    """Serialise a Decimal to its exact string form for storage."""
    return str(amount)


def _convert_decimal(raw: bytes) -> Decimal:
    """Deserialise stored bytes back into an exact Decimal."""
    return Decimal(raw.decode("utf-8"))


def _decimal_add(left: object, right: object) -> str:
    """Add two stored decimal operands exactly and return the text form."""
    return str(Decimal(str(left)) + Decimal(str(right)))


def resolve_db_path() -> Path:
    """Resolve the storage.db path, honouring the override env var."""
    db_path = os.environ.get("MPT_DB_PATH")
    return Path(db_path) if db_path else DEFAULT_DB_PATH


class SqliteDatabase:
    """SQLite store opened from a file path, handing out accumulation repositories."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        """Open the connection and register the Decimal helpers and busy timeout."""
        sqlite3.register_adapter(Decimal, _adapt_decimal)
        sqlite3.register_converter("DECIMAL", _convert_decimal)
        connection = await aiosqlite.connect(
            self._path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        await connection.create_function("decimal_add", 2, _decimal_add, deterministic=True)
        await connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        self._connection = connection
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def connection(self) -> aiosqlite.Connection:
        """Return the underlying SQLite connection."""
        if self._connection is None:
            raise RuntimeError("Database is not open; enter the 'async with' context first.")
        return self._connection

    def subscription_repository(self) -> SubscriptionAccumulationRepository:
        """Return the subscription monthly accumulation repository."""
        return repositories.SubscriptionAccumulationRepository(self.connection)

    def agreement_repository(self) -> AgreementAccumulationRepository:
        """Return the agreement monthly accumulation repository."""
        return repositories.AgreementAccumulationRepository(self.connection)

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        await self.connection.close()
