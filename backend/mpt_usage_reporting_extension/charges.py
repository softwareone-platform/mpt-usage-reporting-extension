import datetime as dt
import logging
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

import typer
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from rich.console import Console
from rich.table import Table

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation, ChargeTotals
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.types import Month, Year

logger = logging.getLogger(__name__)

_AGREEMENT_ID = "agreement.id"
_SUBSCRIPTION_ID = "subscription.id"
_PRICE_PPX1 = "price.ppx1"
_PRICE_SPX1 = "price.spx1"
_STATEMENT_CANCELLED_AT = "statement.audit.cancelled.at"
_STATEMENT_ISSUED_AT = "statement.audit.issued.at"
_UNKNOWN_ID = "-"
_AGREEMENT_ADDITIONAL = "agreement_additional"
_UNKNOWN_YEAR_MONTH: tuple[None, None] = (None, None)

_REPORT_HEADERS = ("Agreement ID", "Subscription ID", "Year", "Month", "PPx1", "SPx1")


class ChargeStreamer:
    """Stream charges for each selected statement without buffering."""

    def stream(self, ctx: RunContext) -> Iterator[StatementCharge]:
        """Yield charges for every selected statement, one statement at a time.

        Calls ``GET /public/v1/billing/statements/{id}/charges`` via the JSONL
        streaming endpoint, so charges are yielded line by line without buffering the
        whole response in memory. The owning statement is attached to each charge as
        ``charge.statement`` so the accumulation month can be derived from it.
        """
        for statement in ctx.statements:
            logger.info("Streaming charges for statement %s", statement.id)
            for charge in ctx.api_client.billing.statements.charges(statement.id).stream():
                charge.statement = statement
                yield charge


class ChargeAccumulator:
    """Accumulate streamed charges into per (agreement, subscription, month) totals."""

    def accumulate(self, charges: Iterable[StatementCharge]) -> ChargeTotals:
        """Consume the charge stream once, summing prices per accumulation key.

        Charges are grouped by ``(agreement_id, subscription_id, year, month)``, where the
        year and month are derived from the charge's owning statement. Only the aggregate
        ``ChargeTotals`` is retained: charges are read one at a time and never collected
        into a list. Persisting the buckets is a separate step.
        """
        totals = ChargeTotals()
        for charge in charges:
            totals.charge_count += 1
            bucket = self._bucket(totals, charge)
            bucket.ppx1 += self._decimal(charge, _PRICE_PPX1)
            bucket.spx1 += self._decimal(charge, _PRICE_SPX1)
        return totals

    def _bucket(self, totals: ChargeTotals, charge: StatementCharge) -> ChargeAccumulation:
        agreement_id = self._path(charge, _AGREEMENT_ID) or _UNKNOWN_ID
        subscription_id = (
            self._path(charge, _SUBSCRIPTION_ID) or f"{_AGREEMENT_ADDITIONAL}_{agreement_id}"
        )
        year, month = self._year_month(charge)
        key = (agreement_id, subscription_id, year, month)
        return totals.accumulations.setdefault(
            key,
            ChargeAccumulation(agreement_id, subscription_id, year, month),
        )

    def _year_month(self, charge: StatementCharge) -> tuple[Year | None, Month | None]:
        """Derive ``(year, month)`` from the owning statement's dates.

        Uses the statement's cancelled date, falling back to its issued date, and returns
        ``(None, None)`` when neither is present or parseable.
        """
        raw = self._path(charge, _STATEMENT_CANCELLED_AT) or self._path(
            charge,
            _STATEMENT_ISSUED_AT,
        )
        if raw is None:
            return _UNKNOWN_YEAR_MONTH
        try:
            moment = dt.datetime.fromisoformat(raw)
        except ValueError:
            return _UNKNOWN_YEAR_MONTH
        return moment.year, Month(moment.month)

    def _path(self, charge: StatementCharge, path: str) -> str | None:
        """Walk a dot-notated attribute path, returning None when any segment is missing."""
        current: Any = charge
        for attr in path.split("."):
            current = getattr(current, attr, None)
            if current is None:
                return None
        return str(current)

    def _decimal(self, charge: StatementCharge, path: str) -> Decimal:
        """Read a dot-notated numeric field as ``Decimal``, defaulting to zero when absent."""
        rendered = self._path(charge, path)
        if rendered is None:
            return Decimal(0)
        return Decimal(rendered)


class ChargeReport:
    """Render the accumulated charge totals as a console table."""

    def __init__(self, totals: ChargeTotals) -> None:
        self._totals = totals

    def render(self) -> None:
        """Print the run summary line and, when charges were streamed, the table."""
        totals = self._totals
        typer.echo(
            f"Streamed {totals.charge_count} charge(s) "
            f"into {len(totals.accumulations)} accumulation(s)",
        )
        if totals.accumulations:
            Console().print(self._table())

    def _table(self) -> Table:
        table = Table(*_REPORT_HEADERS)
        for accumulation in self._totals.accumulations.values():
            table.add_row(
                accumulation.agreement_id,
                accumulation.subscription_id,
                _cell(accumulation.year),
                _cell(accumulation.month),
                str(accumulation.ppx1),
                str(accumulation.spx1),
            )
        return table


def _cell(number: Year | Month | None) -> str:
    """Render a table cell, showing a dash for a missing year or month."""
    if number is None:
        return _UNKNOWN_ID
    return str(number)
