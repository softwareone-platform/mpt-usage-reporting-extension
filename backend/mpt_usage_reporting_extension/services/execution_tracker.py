from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mpt_usage_reporting_extension.persistence.protocols import (
    ExecutionRepository,
    StatementProcessingRepository,
)
from mpt_usage_reporting_extension.types import Command, ExecutionStatus, StatementStatus


@dataclass
class Execution:
    """Mutable handle for the in-flight execution row."""

    id: int
    result: dict[str, object] = field(default_factory=dict)
    has_errors: bool = False

    def record_result(self, **fields: object) -> None:
        """Merge summary fields into the result payload."""
        self.result.update(fields)


class ExecutionTracker:
    """Bracket a command run with a command_execution insight row."""

    def __init__(self, executions: ExecutionRepository) -> None:
        self._executions = executions

    @asynccontextmanager
    async def track(
        self, command: Command, parameters: Mapping[str, object]
    ) -> AsyncIterator[Execution]:
        """Open an execution row, yield its handle, then finalise its status and result.

        A clean exit is ``success``, unless the handle's ``has_errors`` flag is set (partial
        failure, e.g. estimate uploads), which yields ``completed_with_errors``. Any exception
        escaping the body finalises the row as ``failed`` (with the error in the result) and
        re-raises.
        """
        execution = Execution(await self._executions.start(command, parameters))
        try:
            yield execution
        except BaseException as exc:  # noqa: WPS424  (includes typer.Exit)
            execution.record_result(error=str(exc))
            await self._executions.finish(execution.id, ExecutionStatus.FAILED, execution.result)
            raise
        else:
            status = (
                ExecutionStatus.COMPLETED_WITH_ERRORS
                if execution.has_errors
                else ExecutionStatus.SUCCESS
            )
            await self._executions.finish(execution.id, status, execution.result)


class StatementProcessingRecorder:
    """Bracket per-statement work with a statement_processing insight row."""

    def __init__(self, repository: StatementProcessingRepository, execution_id: int) -> None:
        self._repository = repository
        self._execution_id = execution_id

    @asynccontextmanager
    async def record(self, statement_id: str) -> AsyncIterator[None]:
        """Open a processing row for the statement, then finalise it on exit.

        A clean exit is ``success``; any exception escaping the body finalises the row as
        ``failure`` (with the error message) and re-raises.
        """
        processing_id = await self._repository.start(self._execution_id, statement_id)
        try:
            yield
        except BaseException as exc:  # noqa: WPS424
            await self._repository.finish(processing_id, StatementStatus.FAILURE, str(exc))
            raise
        else:
            await self._repository.finish(processing_id, StatementStatus.SUCCESS)
