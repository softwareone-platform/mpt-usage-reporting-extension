import asyncio
from typing import Annotated

import typer
from mpt_extension_sdk.observability import trace_span

from mpt_usage_reporting_extension.persistence.postgres.database import (
    PostgresDatabase,
    resolve_database_url,
)
from mpt_usage_reporting_extension.services.execution_status import StatusReport

_DEFAULT_LIMIT = 10


def status_command(
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="How many recent command executions to show."),
    ] = _DEFAULT_LIMIT,
) -> None:
    """Show the most recent command executions and their status."""
    asyncio.run(status(limit))


@trace_span(
    "usage_reporting.status",
    attributes={"usage_reporting.limit": lambda limit: limit},
)
async def status(limit: int) -> None:
    """Open the store, collect the most recent executions, and render the status table."""
    async with PostgresDatabase(resolve_database_url()) as db:
        executions = [execution async for execution in db.execution_repository().recent(limit)]
    StatusReport(executions).render()
