import logging
from collections.abc import AsyncIterator

import typer
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from mpt_api_client.resources.billing.statements import Statement
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService
from rich.console import Console
from rich.table import Table

from mpt_usage_reporting_extension.accumulation import (
    ChargeAccumulation,
    ChargeTotals,
    StatementChargeFilter,
)
from mpt_usage_reporting_extension.services.execution_tracker import StatementProcessingRecorder

logger = logging.getLogger(__name__)

_REPORT_HEADERS = ("Agreement ID", "Subscription ID", "Year", "Month", "PPx1", "SPx1")


class ChargeStreamer:
    """Stream charges for each selected statement without buffering."""

    def __init__(self, api_service: MPTAPIService, recorder: StatementProcessingRecorder) -> None:
        self._api_service = api_service
        self._recorder = recorder

    async def stream(self, statements: list[Statement]) -> AsyncIterator[StatementCharge]:
        """Yield charges for every selected statement, one statement at a time.

        Calls ``GET /public/v1/billing/statements/{id}/charges`` via the JSONL
        streaming endpoint, so charges are yielded line by line without buffering the
        whole response in memory. The owning statement is attached to each charge as
        ``charge.statement`` so the accumulation month can be derived from it.

        Each statement's streaming is bracketed by a ``statement_processing`` insight row via the
        recorder. Because the charges are yielded from inside that bracket, an error raised
        downstream while consuming a statement's charges is attributed to that statement.
        """
        client = self._api_service.client.billing.statements
        for statement in statements:
            logger.info("Streaming charges for statement %s", statement.id)
            async with self._recorder.record(statement.id):
                async for charge in client.charges(statement.id).stream():
                    charge.statement = statement
                    yield charge


class ChargeAccumulator:
    """Accumulate streamed charges into per (agreement, subscription, month) totals."""

    async def accumulate(
        self,
        charges: AsyncIterator[StatementCharge],
        charge_filter: StatementChargeFilter | None = None,
    ) -> ChargeTotals:
        """Consume the charge stream once, summing prices per accumulation key.

        Charges are grouped by ``(agreement_id, subscription_id, year, month)``, where the
        year and month are derived from the charge's owning statement. Only the aggregate
        ``ChargeTotals`` is retained: charges are read one at a time and never collected
        into a list. Persisting the buckets is a separate step.
        """
        totals = ChargeTotals()
        async for charge in charges:
            if charge_filter is not None and not charge_filter.matches(charge):
                continue
            totals.accumulate(ChargeAccumulation.from_charge(charge))
        return totals


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
