import asyncio
import datetime as dt
from typing import Annotated

import typer

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.settings import ExtensionSettings
from mpt_usage_reporting_extension.utils import to_date  # noqa: WPS347
from mpt_usage_reporting_extension.window import resolve_window


def run(
    date: Annotated[
        dt.datetime | None,
        typer.Option("--date", formats=["%Y-%m-%d"], help="Run for a single UTC day (YYYY-MM-DD)."),
    ] = None,
    from_date: Annotated[
        dt.datetime | None,
        typer.Option("--from-date", formats=["%Y-%m-%d"], help="Window start day, UTC inclusive."),
    ] = None,
    till_date: Annotated[
        dt.datetime | None,
        typer.Option("--till-date", formats=["%Y-%m-%d"], help="Window end day, UTC inclusive."),
    ] = None,
) -> None:
    """Select MPT billing statements for the run window (issued/cancelled two-pass)."""
    window = resolve_window(to_date(date), to_date(from_date), to_date(till_date))
    settings = ExtensionSettings.load()
    ctx = RunContext(
        api_service=build_service(),
        window=window,
        product_ids=settings.product_ids,
    )
    asyncio.run(UsageReportingPipeline(ctx).run())
