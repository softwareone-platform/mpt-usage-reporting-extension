import asyncio
import datetime as dt
import logging
from typing import Annotated

import typer

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.services.accumulation_cleanup import do_cleanup
from mpt_usage_reporting_extension.settings import ExtensionSettings
from mpt_usage_reporting_extension.window import resolve_window

app = typer.Typer(
    help="Report MPT billing subscription usage.",
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """Report MPT billing subscription usage (keeps ``run`` as a named subcommand)."""


@app.command()
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    window = resolve_window(_to_date(date), _to_date(from_date), _to_date(till_date))
    settings = ExtensionSettings.load()
    ctx = RunContext(
        api_service=build_service(),
        window=window,
        product_ids=settings.product_ids,
    )
    asyncio.run(UsageReportingPipeline(ctx).run())


@app.command()
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    anchor = _to_date(date) or _utc_today()
    asyncio.run(do_cleanup(anchor))


def _utc_today() -> dt.date:
    return dt.datetime.now(tz=dt.UTC).date()


def _to_date(parsed: dt.datetime | None) -> dt.date | None:
    if parsed is None:
        return None
    return parsed.date()


def main() -> None:
    """Entry point for the CLI."""
    app()
