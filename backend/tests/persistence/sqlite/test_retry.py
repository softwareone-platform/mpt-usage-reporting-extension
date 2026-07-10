import logging
import sqlite3

import pytest

from mpt_usage_reporting_extension.persistence.sqlite.retry import (
    retry_on_busy,
    retry_on_busy_sync,
)


@pytest.fixture
def mock_sleep(mocker):
    return mocker.patch(
        "mpt_usage_reporting_extension.persistence.sqlite.retry.asyncio.sleep",
        autospec=True,
    )


@pytest.fixture
def mock_sleep_sync(mocker):
    return mocker.patch(
        "mpt_usage_reporting_extension.persistence.sqlite.retry.time.sleep",
        autospec=True,
    )


async def test_returns_result_on_first_attempt(mocker, mock_sleep):
    operation = mocker.AsyncMock(return_value="stored")

    result = await retry_on_busy(operation)()

    assert result == "stored"
    mock_sleep.assert_not_awaited()


@pytest.mark.parametrize("message", ["database is locked", "database is busy"])
async def test_retries_busy_error_then_succeeds(mocker, mock_sleep, message):
    operation = mocker.AsyncMock(side_effect=[sqlite3.OperationalError(message), "stored"])

    result = await retry_on_busy(operation)()

    assert result == "stored"
    mock_sleep.assert_awaited_once_with(0.1)


async def test_raises_after_exhausting_attempts(mocker, mock_sleep):
    operation = mocker.AsyncMock(side_effect=sqlite3.OperationalError("database is locked"))

    with pytest.raises(sqlite3.OperationalError):
        await retry_on_busy(operation)()  # act

    assert operation.await_count == 3
    assert mock_sleep.await_count == 2


async def test_does_not_retry_other_operational_errors(mocker, mock_sleep):
    operation = mocker.AsyncMock(side_effect=sqlite3.OperationalError("no such table: missing"))

    with pytest.raises(sqlite3.OperationalError):
        await retry_on_busy(operation)()  # act

    assert operation.await_count == 1
    mock_sleep.assert_not_awaited()


async def test_logs_busy_retry_at_info_level(mocker, mock_sleep, caplog):
    caplog.set_level(logging.INFO)
    operation = mocker.AsyncMock(
        side_effect=[sqlite3.OperationalError("database is locked"), "stored"]
    )

    await retry_on_busy(operation)()

    assert "SQLite database is busy" in caplog.text


def test_sync_retries_busy_error_then_succeeds(mocker, mock_sleep_sync):
    operation = mocker.Mock(
        side_effect=[sqlite3.OperationalError("database is locked"), "stored"],
    )

    result = retry_on_busy_sync(operation)()

    assert result == "stored"
    mock_sleep_sync.assert_called_once_with(0.1)


def test_sync_raises_after_exhausting_attempts(mocker, mock_sleep_sync):
    operation = mocker.Mock(side_effect=sqlite3.OperationalError("database is locked"))

    with pytest.raises(sqlite3.OperationalError):
        retry_on_busy_sync(operation)()  # act

    assert operation.call_count == 3
    assert mock_sleep_sync.call_count == 2


def test_sync_does_not_retry_other_operational_errors(mocker, mock_sleep_sync):
    operation = mocker.Mock(side_effect=sqlite3.OperationalError("no such table: missing"))

    with pytest.raises(sqlite3.OperationalError):
        retry_on_busy_sync(operation)()  # act

    assert operation.call_count == 1
    mock_sleep_sync.assert_not_called()
