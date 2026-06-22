import datetime as dt
from collections.abc import Iterable

import typer
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
from mpt_usage_reporting_extension.selectors import ProductSelector, Selector
from mpt_usage_reporting_extension.services.accumulation_cleanup import AccumulationCleaner
from mpt_usage_reporting_extension.services.bucket_clean import BucketCleaner
from mpt_usage_reporting_extension.services.charge_persistence import AccumulationPersister
from mpt_usage_reporting_extension.services.charges import (
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
)
from mpt_usage_reporting_extension.services.estimates_uploader import (
    EstimatesUploader,
    updatable_subscription_ids,
)
from mpt_usage_reporting_extension.services.statements import StatementReport, StatementSelector
from mpt_usage_reporting_extension.types import Month
from mpt_usage_reporting_extension.utils import last_month


class UsageReportingPipeline:  # noqa: WPS214
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

    async def recalculate(self, scope: Selector | None) -> None:
        """Reset the scope's buckets, then re-accumulate its statements and push estimates.

        Unlike ``run`` (additive), the scope's buckets are deleted before re-accumulation, so
        re-runs do not double-count. The re-fill selects all of the scope's statements (no date
        window). Retention pruning is skipped.
        """
        async with SqliteDatabase(resolve_db_path()) as db:
            await self._reset(scope, db)
            statements = await self._select_statements()
            accumulations = await self._accumulate_charges(statements)
            await self._persist(
                accumulations, db.subscription_repository(), db.agreement_repository()
            )
            await self._update_estimates(accumulations, db.subscription_repository())

    async def _reset(self, scope: Selector | None, db: SqliteDatabase) -> None:
        """Delete the scope's stored buckets before re-accumulation.

        A ``None`` scope is expanded to the run's configured products, so the reset matches the
        re-fill's product scope instead of wiping buckets of unrelated products.
        """
        api_service = self._ctx.api_service
        cleaner = BucketCleaner(
            db.subscription_repository(),
            db.agreement_repository(),
            api_service.client.commerce.subscriptions,
        )
        if scope is not None:
            await cleaner.clean(scope)
            return
        for product_id in self._ctx.product_ids:
            await cleaner.clean(ProductSelector(product_id))  # noqa: WPS476

    async def _select_statements(self) -> list[Statement]:
        """Select the run window's statements and render the statement report."""
        statements = await StatementSelector(self._ctx.api_service).select(
            self._ctx.window, self._ctx.product_ids, self._ctx.seller_id
        )
        StatementReport(statements, self._ctx.window).render()
        return statements

    async def _accumulate_charges(
        self, statements: list[Statement]
    ) -> Iterable[ChargeAccumulation]:
        """Stream and accumulate the statements' charges, then render the charge report."""
        totals = await ChargeAccumulator().accumulate(
            ChargeStreamer(self._ctx.api_service).stream(statements)
        )
        ChargeReport(totals).render()
        return totals.accumulations.values()

    async def _persist(
        self,
        accumulations: Iterable[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
    ) -> None:
        """Upsert the accumulation buckets into both monthly tables."""
        await AccumulationPersister(subscription_repo, agreement_repo).persist(accumulations)

    async def _update_estimates(
        self,
        accumulations: Iterable[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
    ) -> None:
        """Upload estimates for the run's subscriptions; exit non-zero on failure."""
        anchor = last_month(dt.datetime.now(tz=dt.UTC).date())
        report = await EstimatesUploader(
            subscription_repo, self._ctx.api_service.subscriptions
        ).update(updatable_subscription_ids(accumulations), anchor.year, Month(anchor.month))
        report.render()
        if report.has_failures:
            raise typer.Exit(code=1)

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
