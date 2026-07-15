import os
from typing import Self

import psycopg
from psycopg.rows import DictRow, dict_row

from mpt_usage_reporting_extension.persistence.postgres import insights, repositories
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    ExecutionRepository,
    StatementProcessingRepository,
    SubscriptionAccumulationRepository,
)

_DATABASE_URL_ENV_VAR = "MPT_DATABASE_URL"
_CONNECT_TIMEOUT_SECONDS = 10


def resolve_database_url() -> str:
    """Resolve the PostgreSQL connection URL from the MPT_DATABASE_URL env var."""
    database_url = os.environ.get(_DATABASE_URL_ENV_VAR)
    if not database_url:
        raise RuntimeError("PostgreSQL connection URL is not configured; set MPT_DATABASE_URL.")
    return database_url


def connect_sync() -> psycopg.Connection:
    """Open a synchronous connection for schema migrations."""
    return psycopg.connect(resolve_database_url(), connect_timeout=_CONNECT_TIMEOUT_SECONDS)


class PostgresDatabase:  # noqa: WPS214
    """PostgreSQL store opened from a DSN, handing out accumulation repositories."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._connection: psycopg.AsyncConnection[DictRow] | None = None

    async def __aenter__(self) -> Self:
        """Open a single autocommit connection with dict rows."""
        self._connection = await psycopg.AsyncConnection.connect(
            self._dsn,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def connection(self) -> psycopg.AsyncConnection[DictRow]:
        """Return the underlying PostgreSQL connection."""
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
        """Close and forget the underlying PostgreSQL connection; safe to call repeatedly."""
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None
