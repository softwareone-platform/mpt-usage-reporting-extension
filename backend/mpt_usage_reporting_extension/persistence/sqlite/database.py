import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Self

import aiosqlite

from mpt_usage_reporting_extension.constants import DEFAULT_DB_PATH
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    ExecutionRepository,
    StatementProcessingRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.persistence.sqlite import insights, repositories

_BUSY_TIMEOUT_MS = 5000
# WAL journalling is a persistent DB property and relies on shared memory that is
# unsafe on SMB shares, so every connection pins the rollback journal explicitly.
_JOURNAL_MODE = "DELETE"


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
    """Resolve the database path, honouring the MPT_BSU_DB_PATH override env var."""
    db_path = os.environ.get("MPT_BSU_DB_PATH")
    return Path(db_path) if db_path else DEFAULT_DB_PATH


def connect_sync() -> sqlite3.Connection:
    """Open a synchronous connection with the busy timeout and pinned journal mode."""
    connection = sqlite3.connect(resolve_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000)
    pragmas = (
        f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}",
        f"PRAGMA journal_mode = {_JOURNAL_MODE}",
    )
    try:
        for pragma in pragmas:
            connection.execute(pragma)
    except sqlite3.Error:
        connection.close()
        raise
    return connection


async def _configure(connection: aiosqlite.Connection) -> None:
    """Register the row factory, the decimal_add function, and the shared-volume pragmas."""
    connection.row_factory = sqlite3.Row
    await connection.create_function("decimal_add", 2, _decimal_add, deterministic=True)
    await connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    await connection.execute(f"PRAGMA journal_mode = {_JOURNAL_MODE}")


class SqliteDatabase:  # noqa: WPS214
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
        try:
            await _configure(connection)
        except sqlite3.Error:
            await connection.close()
            raise
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

    def execution_repository(self) -> ExecutionRepository:
        """Return the command-execution insight repository."""
        return insights.ExecutionRepository(self.connection)

    def statement_processing_repository(self) -> StatementProcessingRepository:
        """Return the per-statement processing insight repository."""
        return insights.StatementProcessingRepository(self.connection)

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        await self.connection.close()
