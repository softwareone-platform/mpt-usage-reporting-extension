import json
from collections.abc import AsyncIterator, Mapping

import aiosqlite

from mpt_usage_reporting_extension.persistence.models import ExecutionDetail, ExecutionRecord
from mpt_usage_reporting_extension.persistence.sqlite.repositories import utc_now_iso
from mpt_usage_reporting_extension.types import Command, ExecutionStatus, StatementStatus

_INSERT_EXECUTION = (
    "INSERT INTO command_execution (command, parameters, status, started_at) "
    "VALUES (:command, :parameters, :status, :started_at)"
)
_FINISH_EXECUTION = (
    "UPDATE command_execution SET status = :status, completed_at = :completed_at, "
    "result = :result WHERE id = :id"
)
_INSERT_STATEMENT = (
    "INSERT INTO statement_processing (execution_id, statement_id, started_at, status) "
    "VALUES (:execution_id, :statement_id, :started_at, :status)"
)
_FINISH_STATEMENT = (
    "UPDATE statement_processing SET status = :status, ended_at = :ended_at, "
    "failure_message = :failure_message WHERE id = :id"
)
_RECENT_EXECUTIONS = (
    "SELECT command, status, started_at, completed_at FROM command_execution "
    "ORDER BY started_at DESC, id DESC LIMIT :limit"
)
_GET_EXECUTION = (
    "SELECT id, command, parameters, status, started_at, completed_at, result "
    "FROM command_execution WHERE id = :id"
)


def _require_row_id(row_id: int | None) -> int:
    """Return the inserted row id, guarding the (impossible after INSERT) None case for typing."""
    if row_id is None:  # pragma: no cover - SQLite always sets lastrowid after an INSERT
        raise RuntimeError("INSERT did not produce a row id")
    return row_id


class ExecutionRepository:
    """SQLite-backed command-execution insight repository."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def start(self, command: Command, parameters: Mapping[str, object]) -> int:
        """Insert a running execution row and return its id."""
        cursor = await self._connection.execute(
            _INSERT_EXECUTION,
            {
                "command": str(command),
                "parameters": json.dumps(dict(parameters), default=str),
                "status": str(ExecutionStatus.RUNNING),
                "started_at": utc_now_iso(),
            },
        )
        row_id = cursor.lastrowid
        await cursor.close()
        return _require_row_id(row_id)

    async def finish(
        self, execution_id: int, status: ExecutionStatus, result: Mapping[str, object]
    ) -> None:
        """Stamp completed_at, final status, and the JSON result on the execution row."""
        cursor = await self._connection.execute(
            _FINISH_EXECUTION,
            {
                "id": execution_id,
                "status": str(status),
                "completed_at": utc_now_iso(),
                "result": json.dumps(dict(result), default=str),
            },
        )
        await cursor.close()

    async def get(self, execution_id: int) -> ExecutionDetail | None:
        """Return the execution row by id, or None when absent."""
        async with self._connection.execute(_GET_EXECUTION, {"id": execution_id}) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ExecutionDetail(
            id=row["id"],
            command=row["command"],
            status=row["status"],
            parameters=json.loads(row["parameters"]),
            result=json.loads(row["result"]) if row["result"] else None,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    async def recent(self, limit: int) -> AsyncIterator[ExecutionRecord]:
        """Yield the most recent executions (newest first), capped at limit."""
        async with self._connection.execute(_RECENT_EXECUTIONS, {"limit": limit}) as cursor:
            async for row in cursor:
                yield ExecutionRecord(
                    command=row["command"],
                    status=row["status"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                )


class StatementProcessingRepository:
    """SQLite-backed per-statement processing insight repository."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def start(self, execution_id: int, statement_id: str) -> int:
        """Insert a processing row and return its id."""
        cursor = await self._connection.execute(
            _INSERT_STATEMENT,
            {
                "execution_id": execution_id,
                "statement_id": statement_id,
                "started_at": utc_now_iso(),
                "status": str(StatementStatus.PROCESSING),
            },
        )
        row_id = cursor.lastrowid
        await cursor.close()
        return _require_row_id(row_id)

    async def finish(
        self, processing_id: int, status: StatementStatus, failure_message: str | None = None
    ) -> None:
        """Stamp ended_at, final status, and optional failure message."""
        cursor = await self._connection.execute(
            _FINISH_STATEMENT,
            {
                "id": processing_id,
                "status": str(status),
                "ended_at": utc_now_iso(),
                "failure_message": failure_message,
            },
        )
        await cursor.close()
