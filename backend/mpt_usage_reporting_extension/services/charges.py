import logging
from collections.abc import AsyncIterator

from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from mpt_api_client.resources.billing.statements import Statement
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.accumulation import (
    ChargeAccumulation,
    ChargeTotals,
    StatementChargeFilter,
)
from mpt_usage_reporting_extension.exceptions import UpstreamStatementError
from mpt_usage_reporting_extension.services.execution_tracker import StatementProcessingRecorder
from mpt_usage_reporting_extension.utils import sanitize_log_value

logger = logging.getLogger(__name__)


def _agreement_id(statement: Statement) -> str:
    """The statement's agreement id, or an empty string when the statement carries none."""
    agreement = getattr(statement, "agreement", None)
    return str(getattr(agreement, "id", "") or "")


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
        total = len(statements)
        for position, statement in enumerate(statements, start=1):
            streamed = 0
            logger.info(
                "Streaming charges [%d/%d] statement=%s agreement=%s status=%s",
                position,
                total,
                sanitize_log_value(str(statement.id)),
                sanitize_log_value(_agreement_id(statement)),
                sanitize_log_value(str(getattr(statement, "status", "") or "")),
            )
            async with self._recorder.record(statement.id):
                async for charge in self._stream_statement(statement):
                    streamed += 1
                    yield charge
            logger.info(
                "Streamed %d charge(s) [%d/%d] statement=%s",
                streamed,
                position,
                total,
                sanitize_log_value(str(statement.id)),
            )

    async def _stream_statement(self, statement: Statement) -> AsyncIterator[StatementCharge]:
        """Stream one statement's charges, mapping upstream errors to UpstreamStatementError."""
        client = self._api_service.client.billing.statements
        try:
            async for charge in client.charges(statement.id).stream():
                charge.statement = statement
                yield charge
        except MPTError as exc:
            raise UpstreamStatementError(
                f"Failed to stream charges for statement {statement.id}"
            ) from exc


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
    """Log what the streaming pass accumulated.

    Only the totals: each bucket's own values are logged by ``AccumulationPersister`` as it
    writes them, which reports the same figures plus what it did with them.
    """

    def __init__(self, totals: ChargeTotals) -> None:
        self._totals = totals

    def render(self) -> None:
        """Log the summary line for the streaming pass."""
        totals = self._totals
        logger.info(
            "Streamed %d charge(s) into %d accumulation(s)",
            totals.charge_count,
            len(totals.accumulations),
        )
