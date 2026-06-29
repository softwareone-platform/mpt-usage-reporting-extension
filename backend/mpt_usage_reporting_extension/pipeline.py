import datetime as dt
from collections.abc import Iterable, Mapping

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
from mpt_usage_reporting_extension.services.bucket_delete import BucketDeleter
from mpt_usage_reporting_extension.services.charge_persistence import AccumulationPersister
from mpt_usage_reporting_extension.services.charges import (
    ChargeAccumulator,
    ChargeReport,
    ChargeStreamer,
)
from mpt_usage_reporting_extension.services.estimates_uploader import (
    EstimatesUploader,
    EstimateUploadReport,
    updatable_subscription_ids,
)
from mpt_usage_reporting_extension.services.execution_tracker import (
    Execution,
    ExecutionTracker,
    StatementProcessingRecorder,
)
from mpt_usage_reporting_extension.services.statements import StatementReport, StatementSelector
from mpt_usage_reporting_extension.types import Command, Month
from mpt_usage_reporting_extension.utils import last_month


class UsageReportingPipeline:  # noqa: WPS214
    """Run the end-to-end billing usage reporting pipeline for one run."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx

    async def run(self, parameters: Mapping[str, object]) -> None:
        """Collect charges, persist them, update estimates, then prune old rows."""
        async with SqliteDatabase(resolve_db_path()) as db:
            tracker = ExecutionTracker(db.execution_repository())
            async with tracker.track(Command.RUN, parameters) as execution:
                await self._run(db, execution)
                failed = execution.has_errors
            if failed:
                raise typer.Exit(code=1)

    async def recalculate(self, scope: Selector | None, parameters: Mapping[str, object]) -> None:
        """Delete the scope's buckets, then perform a regular run.

        Unlike ``run`` (additive), the scope's buckets are deleted first, so re-runs do not
        double-count. The re-fill selects all of the scope's statements (no date window). After
        the delete it runs the regular pipeline, including retention pruning.
        """
        async with SqliteDatabase(resolve_db_path()) as db:
            tracker = ExecutionTracker(db.execution_repository())
            async with tracker.track(Command.RECALCULATE, parameters) as execution:
                await self._reset(scope, db)
                await self._run(db, execution)
                failed = execution.has_errors
            if failed:
                raise typer.Exit(code=1)

    async def _run(self, db: SqliteDatabase, execution: Execution) -> None:
        """Collect charges, persist them, update estimates, then prune old rows.

        Estimate-upload failures are recorded on the execution handle (``has_errors``) rather than
        raised here, so the tracked row is finalised as ``completed_with_errors``; the caller turns
        that flag into a non-zero exit after the tracking context closes.
        """
        recorder = StatementProcessingRecorder(db.statement_processing_repository(), execution.id)
        statements = await self._select_statements()
        accumulations = list(await self._accumulate_charges(statements, recorder))
        await self._persist(accumulations, db.subscription_repository(), db.agreement_repository())
        report = await self._update_estimates(accumulations, db.subscription_repository())
        await self._cleanup(db.subscription_repository(), db.agreement_repository())
        execution.record_result(
            statements=len(statements),
            accumulations=len(accumulations),
            estimates_failed=report.failed_count,
        )
        if report.has_failures:
            execution.has_errors = True

    async def _reset(self, scope: Selector | None, db: SqliteDatabase) -> None:
        """Delete the scope's stored buckets before re-accumulation.

        A ``None`` scope is expanded to the run's configured products, so the reset matches the
        re-fill's product scope instead of wiping buckets of unrelated products.
        """
        api_service = self._ctx.api_service
        deleter = BucketDeleter(
            db.subscription_repository(),
            db.agreement_repository(),
            api_service.client.commerce.subscriptions,
        )
        if scope is not None:
            await deleter.delete(scope)
            return
        for product_id in self._ctx.product_ids:
            await deleter.delete(ProductSelector(product_id))  # noqa: WPS476

    async def _select_statements(self) -> list[Statement]:
        """Select the run window's statements and render the statement report."""
        statements = await StatementSelector(self._ctx.api_service).select(
            self._ctx.window, self._ctx.product_ids, self._ctx.seller_id
        )
        StatementReport(statements, self._ctx.window).render()
        return statements

    async def _accumulate_charges(
        self, statements: list[Statement], recorder: StatementProcessingRecorder
    ) -> Iterable[ChargeAccumulation]:
        """Stream and accumulate the statements' charges, then render the charge report."""
        totals = await ChargeAccumulator().accumulate(
            ChargeStreamer(self._ctx.api_service, recorder).stream(statements)
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
    ) -> EstimateUploadReport:
        """Upload estimates for the run's subscriptions and return the upload report.

        The caller inspects the report's failures; this method no longer exits, so the execution
        row can be finalised before the process exits non-zero.
        """
        anchor = last_month(dt.datetime.now(tz=dt.UTC).date())
        report = await EstimatesUploader(
            subscription_repo, self._ctx.api_service.subscriptions
        ).update(updatable_subscription_ids(accumulations), anchor.year, Month(anchor.month))
        report.render()
        return report

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
