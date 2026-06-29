import asyncio
from typing import Annotated

import typer

from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.services.execution_status import StatusReport

_DEFAULT_LIMIT = 10


def status(
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="How many recent command executions to show."),
    ] = _DEFAULT_LIMIT,
) -> None:
    """Show the most recent command executions and their status."""
    asyncio.run(_status(limit))


async def _status(limit: int) -> None:
    """Open the store, collect the most recent executions, and render the status table."""
    async with SqliteDatabase(resolve_db_path()) as db:
        executions = [execution async for execution in db.execution_repository().recent(limit)]
    StatusReport(executions).render()
