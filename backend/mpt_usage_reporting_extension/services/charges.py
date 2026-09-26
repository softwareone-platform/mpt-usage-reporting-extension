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
from mpt_usage_reporting_extension.exceptions import ChargePriceError, UpstreamStatementError
from mpt_usage_reporting_extension.services.execution_tracker import StatementProcessingRecorder
from mpt_usage_reporting_extension.utils import sanitize_log_value

logger = logging.getLogger(__name__)


def _agreement_id(statement: Statement) -> str:
    """The statement's agreement id, or an empty string when the statement carries none."""
    agreement = getattr(statement, "agreement", None)
    return str(getattr(agreement, "id", "") or "")


class ChargeStreamer:
    """Stream one statement's charges without buffering."""

    def __init__(self, api_service: MPTAPIService) -> None:
        self._api_service = api_service

    async def stream(self, statement: Statement) -> AsyncIterator[StatementCharge]:
        """Yield the statement's charges line by line, each with the statement attached.

        Calls ``GET /public/v1/billing/statements/{id}/charges`` via the JSONL streaming endpoint,
        so the response is never buffered whole. The owning statement is attached to each charge
        as ``charge.statement`` so the accumulation month can be derived from it.

        Raises:
            UpstreamStatementError: streaming the charges failed upstream.
        """
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
    """Accumulate each statement's charges into per (agreement, subscription, month) totals."""

    def __init__(self, streamer: ChargeStreamer, recorder: StatementProcessingRecorder) -> None:
        self._streamer = streamer
        self._recorder = recorder

    async def accumulate(
        self,
        statements: list[Statement],
        charge_filter: StatementChargeFilter | None = None,
        *,
        isolate_agreements: bool = False,
    ) -> ChargeTotals:
        """Sum every statement's charges per accumulation key, one statement at a time.

        Charges are grouped by ``(agreement_id, subscription_id, year, month)``. Each statement is
        summed into its own totals inside its ``statement_processing`` recording bracket and merged
        only when every charge could be summed, so a statement never counts partly. An error is
        recorded against its statement and propagates, so nothing that leaves the statement out
        is persisted or pushed. With ``isolate_agreements`` a charge that cannot be summed
        (``ChargePriceError``) fails only its agreement instead: the agreement's buckets are
        dropped, its remaining statements are not streamed, and it is listed in the totals'
        ``failed_agreements``. That suits a recalculate, which rebuilds each agreement whole and
        can simply be re-run; the additive daily run must fail whole. Charges are streamed and
        never collected; only the aggregates are kept.

        Raises:
            ChargePriceError: a charge carries no price the accumulation can sum, unless
                ``isolate_agreements``.
            UpstreamStatementError: streaming a statement's charges failed upstream.
        """
        totals = ChargeTotals()
        for position, statement in enumerate(statements, start=1):
            logger.info(
                "Streaming charges [%d/%d] statement=%s agreement=%s status=%s",
                position,
                len(statements),
                sanitize_log_value(str(statement.id)),
                sanitize_log_value(_agreement_id(statement)),
                sanitize_log_value(str(getattr(statement, "status", "") or "")),
            )
            await self._process_statement(  # noqa: WPS476  # one statement at a time
                statement, charge_filter, totals, isolate_agreements=isolate_agreements
            )
        return totals

    async def _process_statement(
        self,
        statement: Statement,
        charge_filter: StatementChargeFilter | None,
        totals: ChargeTotals,
        *,
        isolate_agreements: bool,
    ) -> None:
        """Accumulate the statement, or fail its agreement when isolating and a charge can't be."""
        agreement_id = _agreement_id(statement)
        if agreement_id in totals.failed_agreements:
            logger.info(
                "Leaving out statement %s of a failed agreement",
                sanitize_log_value(str(statement.id)),
            )
            return
        try:
            await self._accumulate_statement(statement, charge_filter, totals)
        except ChargePriceError as exc:
            if not isolate_agreements:
                raise
            self._fail_agreement(totals, agreement_id, exc)

    async def _accumulate_statement(
        self,
        statement: Statement,
        charge_filter: StatementChargeFilter | None,
        totals: ChargeTotals,
    ) -> None:
        """Sum the statement's charges that pass the filter and merge them into the totals."""
        async with self._recorder.record(statement.id):
            statement_totals = await self._sum_statement(statement, charge_filter)
        logger.info(
            "Accumulated %d charge(s) statement=%s",
            statement_totals.charge_count,
            sanitize_log_value(str(statement.id)),
        )
        totals.merge(statement_totals)

    async def _sum_statement(
        self, statement: Statement, charge_filter: StatementChargeFilter | None
    ) -> ChargeTotals:
        """Sum the statement's charges that pass the filter into totals of their own."""
        statement_totals = ChargeTotals()
        async for charge in self._streamer.stream(statement):
            if charge_filter is None or charge_filter.matches(charge):
                statement_totals.accumulate(ChargeAccumulation.from_charge(charge))
        return statement_totals

    def _fail_agreement(
        self, totals: ChargeTotals, agreement_id: str, error: ChargePriceError
    ) -> None:
        """Drop the agreement from the totals, logging why it is left out."""
        logger.error(
            "Leaving out agreement %s: %s",
            sanitize_log_value(agreement_id),
            sanitize_log_value(str(error)),
        )
        totals.fail_agreement(agreement_id, str(error))


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
            "Streamed %d charge(s) into %d accumulation(s), %d agreement(s) failed",
            totals.charge_count,
            len(totals.accumulations),
            len(totals.failed_agreements),
        )
