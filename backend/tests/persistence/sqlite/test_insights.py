import json

import pytest

from mpt_usage_reporting_extension.types import Command, ExecutionStatus, StatementStatus


@pytest.fixture
def executions(db):
    return db.execution_repository()


@pytest.fixture
def statements(db):
    return db.statement_processing_repository()


async def _fetch_one(db, table, row_id):
    async with db.connection.execute(
        f"SELECT * FROM {table} WHERE id = :id",  # noqa: S608
        {"id": row_id},
    ) as cursor:
        return await cursor.fetchone()


async def test_start_inserts_running_execution(db, executions):
    result = await executions.start(Command.RUN, {"date": "2026-06-01"})

    row = await _fetch_one(db, "command_execution", result)
    assert row["command"] == "run"
    assert json.loads(row["parameters"]) == {"date": "2026-06-01"}
    assert row["status"] == ExecutionStatus.RUNNING
    assert row["started_at"]
    assert (row["completed_at"], row["result"]) == (None, None)


async def test_finish_stamps_status_and_result(db, executions):
    execution_id = await executions.start(Command.CLEANUP, {})

    await executions.finish(execution_id, ExecutionStatus.SUCCESS, {"deleted": 3})  # act

    row = await _fetch_one(db, "command_execution", execution_id)
    assert row["status"] == ExecutionStatus.SUCCESS
    assert row["completed_at"]
    assert json.loads(row["result"]) == {"deleted": 3}


async def test_statement_start_inserts_processing_row(db, statements):
    execution_id = 42

    result = await statements.start(execution_id, "BILL-1")

    row = await _fetch_one(db, "statement_processing", result)
    assert row["execution_id"] == execution_id
    assert row["statement_id"] == "BILL-1"
    assert row["status"] == StatementStatus.PROCESSING
    assert row["started_at"]
    assert (row["ended_at"], row["failure_message"]) == (None, None)


async def test_statement_finish_records_success(db, statements):
    processing_id = await statements.start(1, "BILL-1")

    await statements.finish(processing_id, StatementStatus.SUCCESS)  # act

    row = await _fetch_one(db, "statement_processing", processing_id)
    assert row["status"] == StatementStatus.SUCCESS
    assert row["ended_at"]
    assert row["failure_message"] is None


async def test_statement_finish_records_failure_message(db, statements):
    processing_id = await statements.start(1, "BILL-1")

    await statements.finish(processing_id, StatementStatus.FAILURE, "boom")  # act

    row = await _fetch_one(db, "statement_processing", processing_id)
    assert row["status"] == StatementStatus.FAILURE
    assert row["failure_message"] == "boom"
