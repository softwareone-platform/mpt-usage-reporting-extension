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
from mpt_usage_reporting_extension.selectors import ProductSelector, Selector, SubscriptionSelector
from mpt_usage_reporting_extension.services.accumulation_cleanup import AccumulationCleaner
from mpt_usage_reporting_extension.services.bucket_delete import BucketDeleter, DeleteOutcome
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
            await self._run(db)

    async def recalculate(self, scope: Selector | None) -> None:
        """Delete the scope's buckets, then re-accumulate exactly what was reset.

        Unlike ``run`` (additive), the scope's buckets are deleted first, so re-runs do not
        double-count. The re-fill is confined to the buckets the delete removed: it selects the
        reset agreements' statements (no date window), drops accumulations outside the reset scope,
        and skips the agreement table for a subscription scope whose agreement bucket was left
        intact. Retention pruning still runs afterwards.
        """
        async with SqliteDatabase(resolve_db_path()) as db:
            reset, statement_agreements, agreement_ids = await self._reset(scope, db)
            subscription_ids = tuple(dict.fromkeys(reset.subscriptions))
            previous_subscription_ids = self._ctx.subscription_ids
            self._ctx.subscription_ids = subscription_ids or None
            try:
                await self._refill(reset, statement_agreements, agreement_ids, db)
            finally:
                self._ctx.subscription_ids = previous_subscription_ids

    async def _run(self, db: SqliteDatabase) -> None:
        """Collect charges, persist them, update estimates, then prune old rows."""
        statements = await self._select_statements()
        accumulations = await self._accumulate_charges(statements)
        await self._persist(accumulations, db.subscription_repository(), db.agreement_repository())
        await self._update_estimates(accumulations, db.subscription_repository())
        await self._cleanup(db.subscription_repository(), db.agreement_repository())

    async def _refill(
        self,
        reset: DeleteOutcome,
        statement_agreements: frozenset[str],
        agreement_ids: frozenset[str],
        db: SqliteDatabase,
    ) -> None:
        """Re-accumulate only the buckets the reset removed, then prune old rows."""
        statements = await self._select_statements(tuple(sorted(statement_agreements)))
        accumulations = await self._accumulate_charges(statements)
        kept = self._filter_to_reset(accumulations, reset, statement_agreements)
        await self._persist(
            kept,
            db.subscription_repository(),
            db.agreement_repository(),
            agreement_ids=agreement_ids,
        )
        await self._update_estimates(kept, db.subscription_repository())
        await self._cleanup(db.subscription_repository(), db.agreement_repository())

    async def _reset(
        self, scope: Selector | None, db: SqliteDatabase
    ) -> tuple[DeleteOutcome, frozenset[str], frozenset[str]]:
        """Delete the scope's stored buckets and report the reset scope to rebuild.

        A ``None`` scope is expanded to the run's configured products, so the reset matches the
        re-fill's product scope instead of wiping buckets of unrelated products; the per-product
        reset scopes are unioned into one outcome.
        """
        api_service = self._ctx.api_service
        deleter = BucketDeleter(
            db.subscription_repository(),
            db.agreement_repository(),
            api_service.client.commerce.subscriptions,
        )
        if scope is not None:
            reset = await deleter.delete(scope)
            statement_agreements = deleter.statement_agreements
            agreement_ids = frozenset() if isinstance(scope, SubscriptionSelector) else statement_agreements
            return reset, statement_agreements, agreement_ids
        return await self._reset_products(deleter)

    async def _reset_products(
        self, deleter: BucketDeleter
    ) -> tuple[DeleteOutcome, frozenset[str], frozenset[str]]:
        """Delete each configured product's buckets and union their reset scopes."""
        subscriptions: list[str] = []
        agreements: list[str] = []
        statement_agreements: set[str] = set()
        for product_id in self._ctx.product_ids:
            outcome = await deleter.delete(ProductSelector(product_id))  # noqa: WPS476
            subscriptions.extend(outcome.subscriptions)
            agreements.extend(outcome.agreements)
            statement_agreements |= deleter.statement_agreements
        reset = DeleteOutcome(subscriptions=subscriptions, agreements=agreements)
        narrowed = frozenset(statement_agreements)
        return reset, narrowed, narrowed

    def _filter_to_reset(
        self,
        accumulations: Iterable[ChargeAccumulation],
        reset: DeleteOutcome,
        statement_agreements: frozenset[str],
    ) -> list[ChargeAccumulation]:
        """Keep only the accumulations whose bucket the reset removed."""
        return [
            accumulation
            for accumulation in accumulations
            if accumulation.subscription_id in reset.subscriptions
            or accumulation.agreement_id in statement_agreements
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
        self, statements: list[Statement]
    ) -> Iterable[ChargeAccumulation]:
        """Stream and accumulate the statements' charges, then render the charge report."""
        totals = await ChargeAccumulator().accumulate(
            ChargeStreamer(self._ctx.api_service).stream(statements),
            self._ctx.subscription_ids,
        )
        ChargeReport(totals).render()
        return totals.accumulations.values()

    async def _persist(
        self,
        accumulations: Iterable[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
        agreement_ids: frozenset[str] | None = None,
    ) -> None:
        """Upsert the accumulation buckets into the monthly tables.

        ``agreement_ids`` is forwarded to the persister to restrict agreement-table writes during a
        scoped recalculate; ``None`` writes every agreement bucket (the regular run).
        """
        await AccumulationPersister(subscription_repo, agreement_repo).persist(
            accumulations, agreement_ids
        )

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
