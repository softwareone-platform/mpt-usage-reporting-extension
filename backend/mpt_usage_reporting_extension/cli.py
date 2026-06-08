"""Command line interface for the MPT usage reporting extension."""

import datetime as dt
import logging
from typing import Annotated

import typer

from mpt_usage_reporting_extension.charges import (
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
)
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_client
from mpt_usage_reporting_extension.settings import ExtensionSettings
from mpt_usage_reporting_extension.statements import StatementReport, StatementSelector
from mpt_usage_reporting_extension.window import resolve_window

app = typer.Typer(
    help="Report MPT billing subscription usage.",
    no_args_is_help=False,
)

NOT_IMPLEMENTED_ART = r"""
  _   _       _     _                 _                           _           _
 | \ | |     | |   (_)               | |                         | |         | |
 |  \| | ___ | |_   _ _ __ ___  _ __ | | ___ _ __ ___   ___ _ __ | |_ ___  __| |
 | . ` |/ _ \| __| | | '_ ` _ \| '_ \| |/ _ \ '_ ` _ \ / _ \ '_ \| __/ _ \/ _` |
 | |\  | (_) | |_  | | | | | | | |_) | |  __/ | | | | |  __/ | | | ||  __/ (_| |
 |_| \_|\___/ \__| |_|_| |_| |_| .__/|_|\___|_| |_| |_|\___|_| |_|\__\___|\__,_|
                               | |
                               |_|
"""


@app.command()
def billing_subscription_usage() -> None:
    """Report billing subscription usage for an account."""
    typer.secho(NOT_IMPLEMENTED_ART, fg=typer.colors.YELLOW)
    typer.secho("Not implemented", fg=typer.colors.YELLOW)


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
        api_client=build_client(),
        window=window,
        product_ids=settings.product_ids,
    )
    StatementSelector().select(ctx)
    StatementReport(ctx).render()
    charges = ChargeStreamer().stream(ctx)
    totals = ChargeAccumulator().accumulate(charges)
    ChargeReport(totals).render()


def _to_date(parsed: dt.datetime | None) -> dt.date | None:
    if parsed is None:
        return None
    return parsed.date()


def main() -> None:
    """Entry point for the CLI."""
    app()
