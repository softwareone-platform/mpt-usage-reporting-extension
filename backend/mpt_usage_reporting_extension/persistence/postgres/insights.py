import datetime as dt
import json
from collections.abc import AsyncIterator, Mapping

import psycopg
from psycopg.rows import DictRow

from mpt_usage_reporting_extension.persistence.models import ExecutionRecord
from mpt_usage_reporting_extension.persistence.postgres.repositories import utc_now
from mpt_usage_reporting_extension.types import Command, ExecutionStatus, StatementStatus

_INSERT_EXECUTION = (
    "INSERT INTO command_execution (command, parameters, status, started_at) "
    "VALUES (%(command)s, %(parameters)s, %(status)s, %(started_at)s) RETURNING id"
)
_FINISH_EXECUTION = (
    "UPDATE command_execution SET status = %(status)s, completed_at = %(completed_at)s, "
    "result = %(result)s WHERE id = %(id)s"
)
_INSERT_STATEMENT = (
    "INSERT INTO statement_processing (execution_id, statement_id, started_at, status) "
    "VALUES (%(execution_id)s, %(statement_id)s, %(started_at)s, %(status)s) RETURNING id"
)
_FINISH_STATEMENT = (
    "UPDATE statement_processing SET status = %(status)s, ended_at = %(ended_at)s, "
    "failure_message = %(failure_message)s WHERE id = %(id)s"
)
_RECENT_EXECUTIONS = (
    "SELECT command, status, started_at, completed_at FROM command_execution "
    "ORDER BY started_at DESC, id DESC LIMIT %(limit)s"
)


def _require_row_id(row: DictRow | None) -> int:
    """Return the id from an INSERT ... RETURNING row, guarding the impossible None case."""
    if row is None:  # pragma: no cover - RETURNING always produces a row after an INSERT
        raise RuntimeError("INSERT did not produce a row id")
    row_id: int = row["id"]
    return row_id


def _iso_z(moment: dt.datetime) -> str:
    """Format an aware datetime as an ISO8601 string with a Z suffix."""
    return moment.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


class ExecutionRepository:
    """PostgreSQL-backed command-execution insight repository."""

    def __init__(self, connection: psycopg.AsyncConnection[DictRow]) -> None:
        self._connection = connection

    async def start(self, command: Command, parameters: Mapping[str, object]) -> int:
        """Insert a running execution row and return its id."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                _INSERT_EXECUTION,
                {
                    "command": str(command),
                    "parameters": json.dumps(dict(parameters), default=str),
                    "status": str(ExecutionStatus.RUNNING),
                    "started_at": utc_now(),
                },
            )
            return _require_row_id(await cursor.fetchone())

    async def finish(
        self, execution_id: int, status: ExecutionStatus, result: Mapping[str, object]
    ) -> None:
        """Stamp completed_at, final status, and the JSON result on the execution row."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                _FINISH_EXECUTION,
                {
                    "id": execution_id,
                    "status": str(status),
                    "completed_at": utc_now(),
                    "result": json.dumps(dict(result), default=str),
                },
            )

    async def recent(self, limit: int) -> AsyncIterator[ExecutionRecord]:
        """Yield the most recent executions (newest first), capped at limit."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(_RECENT_EXECUTIONS, {"limit": limit})
            async for row in cursor:
                completed_at = row["completed_at"]
                yield ExecutionRecord(
                    command=row["command"],
                    status=row["status"],
                    started_at=_iso_z(row["started_at"]),
                    completed_at=None if completed_at is None else _iso_z(completed_at),
                )


class StatementProcessingRepository:
    """PostgreSQL-backed per-statement processing insight repository."""

    def __init__(self, connection: psycopg.AsyncConnection[DictRow]) -> None:
        self._connection = connection

    async def start(self, execution_id: int, statement_id: str) -> int:
        """Insert a processing row and return its id."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                _INSERT_STATEMENT,
                {
                    "execution_id": execution_id,
                    "statement_id": statement_id,
                    "started_at": utc_now(),
                    "status": str(StatementStatus.PROCESSING),
                },
            )
            return _require_row_id(await cursor.fetchone())

    async def finish(
        self, processing_id: int, status: StatementStatus, failure_message: str | None = None
    ) -> None:
        """Stamp ended_at, final status, and optional failure message."""
        async with self._connection.cursor() as cursor:
            await cursor.execute(
                _FINISH_STATEMENT,
                {
                    "id": processing_id,
                    "status": str(status),
                    "ended_at": utc_now(),
                    "failure_message": failure_message,
                },
            )
