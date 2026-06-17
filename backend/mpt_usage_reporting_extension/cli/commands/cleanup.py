import asyncio
import datetime as dt
from typing import Annotated

import typer

from mpt_usage_reporting_extension.services.accumulation_cleanup import do_cleanup
from mpt_usage_reporting_extension.utils import to_date  # noqa: WPS347


def cleanup(
    date: Annotated[
        dt.datetime | None,
        typer.Option(
            "--date",
            formats=["%Y-%m-%d"],
            help="Retention anchor day (YYYY-MM-DD); defaults to today UTC.",
        ),
    ] = None,
) -> None:
    """Delete accumulation rows older than the rolling 18-month retention window."""
    today = dt.datetime.now(tz=dt.UTC).date()
    anchor = to_date(date) or today
    asyncio.run(do_cleanup(anchor))
