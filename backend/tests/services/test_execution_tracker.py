import pytest

from mpt_usage_reporting_extension.services.execution_tracker import (
    ExecutionTracker,
    StatementProcessingRecorder,
)
from mpt_usage_reporting_extension.types import Command, ExecutionStatus, StatementStatus


@pytest.fixture
def executions(mocker):
    repo = mocker.AsyncMock()
    repo.start.return_value = 11
    return repo


@pytest.fixture
def processing(mocker):
    repo = mocker.AsyncMock()
    repo.start.return_value = 99
    return repo


async def test_track_finishes_success(executions):
    async with ExecutionTracker(executions).track(Command.RUN, {"date": None}) as execution:
        execution.record_result(statements=2)

    executions.start.assert_awaited_once_with(Command.RUN, {"date": None})
    executions.finish.assert_awaited_once_with(11, ExecutionStatus.SUCCESS, {"statements": 2})


async def test_track_finishes_completed_with_errors(executions):
    async with ExecutionTracker(executions).track(Command.RUN, {}) as execution:
        execution.has_errors = True

    executions.finish.assert_awaited_once_with(11, ExecutionStatus.COMPLETED_WITH_ERRORS, {})


async def test_track_finishes_failed_and_reraises(executions):
    with pytest.raises(RuntimeError, match="boom"):
        async with ExecutionTracker(executions).track(Command.DELETE, {}):
            raise RuntimeError("boom")

    executions.finish.assert_awaited_once_with(11, ExecutionStatus.FAILED, {"error": "boom"})


async def test_resume_finishes_success_without_starting(executions):
    async with ExecutionTracker(executions).resume(7) as execution:
        execution.record_result(statements=3)

    executions.start.assert_not_awaited()
    executions.finish.assert_awaited_once_with(7, ExecutionStatus.SUCCESS, {"statements": 3})


async def test_resume_finishes_failed_and_reraises(executions):
    with pytest.raises(RuntimeError, match="boom"):
        async with ExecutionTracker(executions).resume(7):
            raise RuntimeError("boom")

    executions.finish.assert_awaited_once_with(7, ExecutionStatus.FAILED, {"error": "boom"})


async def test_recorder_records_success(processing):
    async with StatementProcessingRecorder(processing, execution_id=5).record("BILL-1"):
        processing.start.assert_awaited_once_with(5, "BILL-1")

    processing.finish.assert_awaited_once_with(99, StatementStatus.SUCCESS)


async def test_recorder_records_failure_and_reraises(processing):
    with pytest.raises(ValueError, match="bad"):
        async with StatementProcessingRecorder(processing, execution_id=5).record("BILL-1"):
            raise ValueError("bad")

    processing.finish.assert_awaited_once_with(99, StatementStatus.FAILURE, "bad")
