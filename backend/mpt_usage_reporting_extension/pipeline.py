import contextlib
import datetime as dt
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

import typer
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation, StatementChargeFilter
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.persistence.postgres.database import (
    PostgresDatabase,
    resolve_database_url,
)
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    Database,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.selectors import ProductSelector, Selector, SubscriptionSelector
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


@dataclass(frozen=True)
class ResetScope:
    """The selector-defined scope a recalculate deletes and rebuilds."""

    subscriptions: frozenset[str]
    statement_agreements: frozenset[str]
    agreement_ids: frozenset[str]


class UsageReportingPipeline:  # noqa: WPS214
    """Run the end-to-end billing usage reporting pipeline for one run."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx

    async def run(self, parameters: Mapping[str, object]) -> None:
        """Collect charges, persist them, update estimates, then prune old rows."""
        async with PostgresDatabase(resolve_database_url()) as db:
            tracker = ExecutionTracker(db.execution_repository())
            async with tracker.track(Command.RUN, parameters) as execution:
                await self._run(db, execution)
                failed = execution.has_errors
            if failed:
                raise typer.Exit(code=1)

    async def recalculate(
        self, scope: Selector | None, parameters: Mapping[str, object], *, dry_run: bool = False
    ) -> None:
        """Delete the scope's buckets, then re-accumulate the selector's scope.

        Unlike ``run`` (additive), the scope's buckets are deleted first, so re-runs do not
        double-count. The rebuild scope is defined by the selector — the agreements it resolves (or
        the given agreement/subscription id) — not by what the delete removed, so a recalculate
        also bootstraps buckets with no stored rows (e.g. an empty database). It selects the scope
        agreements' statements (no date window), drops accumulations outside the scope, and skips
        the agreement table for a subscription scope whose shared agreement bucket is left intact.
        Retention pruning still runs afterwards.
        When ``dry_run`` is enabled, every read/compute stage still runs, but all DB delete/upsert/
        prune actions and subscription estimate updates are replaced by no-ops.
        """
        async with PostgresDatabase(resolve_database_url()) as db:
            if dry_run:
                typer.echo("Dry run: running recalculate in read-only mode (no writes or updates).")

            tracker = ExecutionTracker(db.execution_repository())
            async with tracker.track(Command.RECALCULATE, parameters) as execution:
                reset_scope = await self._reset(scope, db, dry_run=dry_run)
                with self._scoped_charge_filter(reset_scope.subscriptions):
                    await self._refill(reset_scope, db, execution, dry_run=dry_run)
                failed = execution.has_errors
            if failed:
                raise typer.Exit(code=1)

    @contextlib.contextmanager
    def _scoped_charge_filter(self, subscriptions: Iterable[str]) -> Iterator[None]:
        """Apply the reset subscriptions' charge filter, restoring it even if the re-fill raises."""
        previous_filter = self._ctx.charge_filter
        self._ctx.charge_filter = StatementChargeFilter.for_subscriptions(subscriptions)
        try:  # noqa: WPS501  # restore the filter even when the re-fill raises
            yield
        finally:
            self._ctx.charge_filter = previous_filter

    async def _run(self, db: Database, execution: Execution) -> None:
        """Collect charges, persist them, update estimates, then prune old rows.

        Estimate-upload failures are recorded on the execution handle (``has_errors``) rather than
        raised here, so the tracked row is finalised as ``completed_with_errors``; the caller turns
        that flag into a non-zero exit after the tracking context closes.
        """
        recorder = StatementProcessingRecorder(db.statement_processing_repository(), execution.id)
        statements = await self._select_statements()
        accumulations = list(await self._accumulate_charges(statements, recorder))
        await self._persist(accumulations, db.subscription_repository(), db.agreement_repository())
        report = await self._update_estimates(
            accumulations,
            db.subscription_repository(),
            subscriptions=self._ctx.api_service.subscriptions,
            dry_run=False,
        )
        await self._cleanup(db.subscription_repository(), db.agreement_repository())
        execution.record_result(
            statements=len(statements),
            accumulations=len(accumulations),
            estimates_failed=report.failed_count,
        )
        if report.has_failures:
            execution.has_errors = True

    async def _refill(
        self,
        reset_scope: ResetScope,
        db: Database,
        execution: Execution,
        *,
        dry_run: bool,
    ) -> None:
        """Re-accumulate the selector-defined reset scope's buckets, then prune old rows."""
        recorder = StatementProcessingRecorder(db.statement_processing_repository(), execution.id)
        statements = await self._select_statements(tuple(sorted(reset_scope.statement_agreements)))
        accumulations = await self._accumulate_charges(statements, recorder)
        kept = self._filter_to_reset(accumulations, reset_scope)
        await self._persist(
            kept,
            db.subscription_repository(),
            db.agreement_repository(),
            agreement_ids=reset_scope.agreement_ids,
            dry_run=dry_run,
        )
        report = await self._update_estimates(
            kept,
            db.subscription_repository(),
            subscriptions=self._ctx.api_service.subscriptions,
            dry_run=dry_run,
        )
        await self._cleanup(
            db.subscription_repository(),
            db.agreement_repository(),
            dry_run=dry_run,
        )
        execution.record_result(
            statements=len(statements),
            accumulations=len(kept),
            estimates_failed=report.failed_count,
        )
        if report.has_failures:
            execution.has_errors = True

    async def _reset(
        self,
        scope: Selector | None,
        db: Database,
        *,
        dry_run: bool,
    ) -> ResetScope:
        """Delete the scope's stored buckets and report the selector-defined scope to rebuild.

        A ``None`` scope is expanded to the run's configured products, so the reset matches the
        re-fill's product scope instead of wiping buckets of unrelated products; the per-product
        reset scopes are unioned into one outcome.
        """
        api_service = self._ctx.api_service
        deleter = BucketDeleter(
            db.subscription_repository(),
            db.agreement_repository(),
            api_service.client.commerce.subscriptions,
            dry_run=dry_run,
        )
        if scope is not None:
            reset = await deleter.delete(scope)
            statement_agreements = deleter.statement_agreements
            if isinstance(scope, SubscriptionSelector):
                return ResetScope(
                    subscriptions=frozenset((scope.subscription_id,)),
                    statement_agreements=statement_agreements,
                    agreement_ids=frozenset(),
                )
            return ResetScope(
                subscriptions=frozenset(reset.subscriptions),
                statement_agreements=statement_agreements,
                agreement_ids=statement_agreements,
            )
        return await self._reset_products(deleter)

    async def _reset_products(self, deleter: BucketDeleter) -> ResetScope:
        """Delete each configured product's buckets and union their reset scopes."""
        subscriptions: set[str] = set()
        statement_agreements: set[str] = set()
        for product_id in self._ctx.product_ids:
            outcome = await deleter.delete(ProductSelector(product_id))  # noqa: WPS476
            subscriptions |= set(outcome.subscriptions)
            statement_agreements |= deleter.statement_agreements
        narrowed = frozenset(statement_agreements)
        return ResetScope(frozenset(subscriptions), narrowed, narrowed)

    def _filter_to_reset(
        self, accumulations: Iterable[ChargeAccumulation], reset: ResetScope
    ) -> list[ChargeAccumulation]:
        """Keep only the accumulations inside the selector's reset scope."""
        return [
            accumulation
            for accumulation in accumulations
            if accumulation.subscription_id in reset.subscriptions
            or accumulation.agreement_id in reset.statement_agreements
        ]

    async def _select_statements(self, agreement_ids: tuple[str, ...] = ()) -> list[Statement]:
        """Select the run window's statements and render the statement report.

        When ``agreement_ids`` is given the selection is narrowed to those agreements; otherwise it
        uses the run's configured products (and optional seller).
        """
        statements = await StatementSelector(self._ctx.api_service).select(
            self._ctx.window, self._ctx.product_ids, self._ctx.seller_id, agreement_ids
        )
        StatementReport(statements, self._ctx.window).render()
        return statements

    async def _accumulate_charges(
        self, statements: list[Statement], recorder: StatementProcessingRecorder
    ) -> Iterable[ChargeAccumulation]:
        """Stream and accumulate the statements' charges, then render the charge report."""
        totals = await ChargeAccumulator().accumulate(
            ChargeStreamer(self._ctx.api_service, recorder).stream(statements),
            self._ctx.charge_filter,
        )
        ChargeReport(totals).render()
        return totals.accumulations.values()

    async def _persist(
        self,
        accumulations: Iterable[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
        agreement_ids: frozenset[str] | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        """Upsert the accumulation buckets into the monthly tables.

        ``agreement_ids`` is forwarded to the persister to restrict agreement-table writes during a
        scoped recalculate; ``None`` writes every agreement bucket (the regular run).
        """
        await AccumulationPersister(
            subscription_repo,
            agreement_repo,
            dry_run=dry_run,
        ).persist(accumulations, agreement_ids)

    async def _update_estimates(
        self,
        accumulations: Iterable[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
        *,
        subscriptions: object,
        dry_run: bool,
    ) -> EstimateUploadReport:
        """Upload estimates for the run's subscriptions and return the upload report.

        The caller inspects the report's failures; this method no longer exits, so the execution
        row can be finalised before the process exits non-zero.
        """
        anchor = last_month(dt.datetime.now(tz=dt.UTC).date())
        report = await EstimatesUploader(
            subscription_repo,
            subscriptions,  # type: ignore[arg-type]
            dry_run=dry_run,
        ).update(updatable_subscription_ids(accumulations), anchor.year, Month(anchor.month))
        report.render()
        return report

    async def _cleanup(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
        *,
        dry_run: bool = False,
    ) -> None:
        """Prune both tables to the rolling 18-month retention window ending this month (UTC)."""
        today = dt.datetime.now(tz=dt.UTC).date()
        await AccumulationCleaner(
            subscription_repo,
            agreement_repo,
            dry_run=dry_run,
        ).cleanup(today.year, Month(today.month))
