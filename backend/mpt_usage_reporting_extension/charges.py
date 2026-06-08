"""Stream and accumulate billing statement charges for the ``run`` command."""

import datetime as dt
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import typer
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from rich.console import Console
from rich.table import Table

from mpt_usage_reporting_extension.context import RunContext

logger = logging.getLogger(__name__)

_AGREEMENT_ID = "agreement.id"
_SUBSCRIPTION_ID = "subscription.id"
_PRICE_PPX1 = "price.ppx1"
_PRICE_SPX1 = "price.spx1"
_AUDIT_CREATED_AT = "audit.created.at"
_UNKNOWN_ID = "-"
_AGREEMENT_ADDITIONAL = "agreement_additional"
_UNKNOWN_YEAR_MONTH = (0, 0)

_REPORT_HEADERS = ("Agreement ID", "Subscription ID", "Year", "Month", "PPx1", "SPx1")

#: Bucket key ordered as agreement id, subscription id, year, then month.
AccumulationKey = tuple[str, str, int, int]


@dataclass
class ChargeAccumulation:
    """Accumulated price totals for one (agreement, subscription, year, month) bucket."""

    agreement_id: str
    subscription_id: str
    year: int
    month: int
    ppx1: Decimal = Decimal(0)
    spx1: Decimal = Decimal(0)


@dataclass
class ChargeTotals:
    """Aggregate charge totals for a run, grouped per accumulation key."""

    charge_count: int = 0
    accumulations: dict[AccumulationKey, ChargeAccumulation] = field(default_factory=dict)


class ChargeStreamer:
    """Stream charges for each selected statement without buffering."""

    def stream(self, ctx: RunContext) -> Iterator[StatementCharge]:
        """Yield charges for every selected statement, one statement at a time.

        Calls ``GET /public/v1/billing/statements/{id}/charges`` via the JSONL
        streaming endpoint, so charges are yielded line by line without buffering the
        whole response in memory.
        """
        for statement in ctx.statements:
            logger.info("Streaming charges for statement %s", statement.id)
            yield from ctx.api_client.billing.statements.charges(statement.id).stream()


class ChargeAccumulator:
    """Accumulate streamed charges into per (agreement, subscription, month) totals."""

    def accumulate(self, charges: Iterable[StatementCharge]) -> ChargeTotals:
        """Consume the charge stream once, summing prices per accumulation key.

        Charges are grouped by ``(agreement_id, subscription_id, year, month)``. Only the
        aggregate ``ChargeTotals`` is retained: charges are read one at a time and never
        collected into a list.
        """
        totals = ChargeTotals()
        for charge in charges:
            totals.charge_count += 1
            bucket = self._bucket(totals, charge)
            bucket.ppx1 += _decimal(charge, _PRICE_PPX1)
            bucket.spx1 += _decimal(charge, _PRICE_SPX1)
        return totals

    def _bucket(self, totals: ChargeTotals, charge: StatementCharge) -> ChargeAccumulation:
        agreement_id = _path(charge, _AGREEMENT_ID) or _UNKNOWN_ID
        subscription_id = _path(charge, _SUBSCRIPTION_ID) or _AGREEMENT_ADDITIONAL
        year, month = self._year_month(charge)
        key = (agreement_id, subscription_id, year, month)
        return totals.accumulations.setdefault(
            key,
            ChargeAccumulation(agreement_id, subscription_id, year, month),
        )

    def _year_month(self, charge: StatementCharge) -> tuple[int, int]:
        """Derive ``(year, month)`` from the charge's created date, ``(0, 0)`` if absent."""
        created = _path(charge, _AUDIT_CREATED_AT)
        if created is None:
            return _UNKNOWN_YEAR_MONTH
        try:
            moment = dt.datetime.fromisoformat(created)
        except ValueError:
            return _UNKNOWN_YEAR_MONTH
        return moment.year, moment.month


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
                str(accumulation.year),
                str(accumulation.month),
                str(accumulation.ppx1),
                str(accumulation.spx1),
            )
        return table


def _path(charge: StatementCharge, path: str) -> str | None:
    """Walk a dot-notated attribute path, returning None when any segment is missing."""
    current: Any = charge
    for attr in path.split("."):
        current = getattr(current, attr, None)
        if current is None:
            return None
    return str(current)


def _decimal(charge: StatementCharge, path: str) -> Decimal:
    """Read a dot-notated numeric field as ``Decimal``, defaulting to zero when absent."""
    rendered = _path(charge, path)
    if rendered is None:
        return Decimal(0)
    return Decimal(rendered)
