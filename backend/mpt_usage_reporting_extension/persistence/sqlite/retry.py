import asyncio
import functools
import logging
import sqlite3
import time
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 0.1
_BUSY_MESSAGES = ("database is locked", "database is busy")


def _is_busy(error: sqlite3.OperationalError) -> bool:
    return any(message in str(error) for message in _BUSY_MESSAGES)


def _log_busy_retry(error: sqlite3.OperationalError, attempt: int) -> None:
    logger.info(
        "SQLite database is busy (%s); retrying write, attempt %s of %s",
        error,
        attempt,
        _MAX_ATTEMPTS - 1,
    )


async def _delay_if_busy(error: sqlite3.OperationalError, attempt: int) -> None:
    if not _is_busy(error):
        raise error
    _log_busy_retry(error, attempt)
    await asyncio.sleep(_RETRY_DELAY_SECONDS * attempt)


def _delay_if_busy_sync(error: sqlite3.OperationalError, attempt: int) -> None:
    if not _is_busy(error):
        raise error
    _log_busy_retry(error, attempt)
    time.sleep(_RETRY_DELAY_SECONDS * attempt)


def retry_on_busy[**Args, Result](
    operation: Callable[Args, Coroutine[Any, Any, Result]],
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    """Retry the wrapped write when SQLite reports the database busy or locked.

    Retries up to two times with a small linear backoff before the final attempt;
    any other error propagates immediately.
    """

    @functools.wraps(operation)
    async def wrapper(*args: Args.args, **kwargs: Args.kwargs) -> Result:
        for attempt in range(1, _MAX_ATTEMPTS):
            try:
                return await operation(*args, **kwargs)  # noqa: WPS476
            except sqlite3.OperationalError as error:
                await _delay_if_busy(error, attempt)  # noqa: WPS476
        return await operation(*args, **kwargs)

    return wrapper


def retry_on_busy_sync[**Args, Result](
    operation: Callable[Args, Result],
) -> Callable[Args, Result]:
    """Retry the wrapped synchronous write when SQLite reports the database busy or locked.

    Retries up to two times with a small linear backoff before the final attempt;
    any other error propagates immediately.
    """

    @functools.wraps(operation)
    def wrapper(*args: Args.args, **kwargs: Args.kwargs) -> Result:
        for attempt in range(1, _MAX_ATTEMPTS):
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as error:
                _delay_if_busy_sync(error, attempt)
        return operation(*args, **kwargs)

    return wrapper
