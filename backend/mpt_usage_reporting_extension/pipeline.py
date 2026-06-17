import datetime as dt

from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.services.accumulation_cleanup import AccumulationCleaner
from mpt_usage_reporting_extension.services.charge_persistence import AccumulationPersister
from mpt_usage_reporting_extension.services.charges import (
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
)
from mpt_usage_reporting_extension.services.statements import StatementReport, StatementSelector
from mpt_usage_reporting_extension.services.subscription_estimates import (
    SubscriptionEstimateUpdater,
)
from mpt_usage_reporting_extension.types import Month


class UsageReportingPipeline:
    """Run the end-to-end billing usage reporting pipeline for one run."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx

    async def run(self) -> None:
        """Collect charges, persist them, update estimates, then prune old rows."""
        async with SqliteDatabase(resolve_db_path()) as db:
            statements = await self._select_statements()
            accumulations = await self._accumulate_charges(statements)
            await self._persist(
                accumulations, db.subscription_repository(), db.agreement_repository()
            )
            await self._update_estimates(accumulations, db.subscription_repository())
            await self._cleanup(db.subscription_repository(), db.agreement_repository())

    async def _select_statements(self) -> list[Statement]:
        """Select the run window's statements and render the statement report."""
        statements = await StatementSelector(self._ctx.api_service).select(
            self._ctx.window, self._ctx.product_ids, self._ctx.seller_id
        )
        StatementReport(statements, self._ctx.window).render()
        return statements

    async def _accumulate_charges(self, statements: list[Statement]) -> list[ChargeAccumulation]:
        """Stream and accumulate the statements' charges, then render the charge report."""
        totals = await ChargeAccumulator().accumulate(
            ChargeStreamer(self._ctx.api_service).stream(statements)
        )
        ChargeReport(totals).render()
        return list(totals.accumulations.values())

    async def _persist(
        self,
        accumulations: list[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
    ) -> None:
        """Upsert the accumulation buckets into both monthly tables."""
        await AccumulationPersister(subscription_repo, agreement_repo).persist(accumulations)

    async def _update_estimates(
        self,
        accumulations: list[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
    ) -> None:
        """PUT the computed price estimates to each real subscription."""
        await SubscriptionEstimateUpdater(
            subscription_repo, self._ctx.api_service.subscriptions
        ).update(accumulations)

    async def _cleanup(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
    ) -> None:
        """Prune both tables to the rolling 18-month retention window ending this month (UTC)."""
        today = dt.datetime.now(tz=dt.UTC).date()
        await AccumulationCleaner(subscription_repo, agreement_repo).cleanup(
            today.year, Month(today.month)
        )
