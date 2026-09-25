import contextlib
import datetime as dt
import functools
import logging
import sys
import traceback
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass

import typer
from mpt_api_client.resources.billing.statements import Statement
from mpt_extension_sdk.observability import trace_span

from mpt_usage_reporting_extension.accumulation import (
    ChargeAccumulation,
    ChargeTotals,
    StatementChargeFilter,
)
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
from mpt_usage_reporting_extension.services.estimate_currency import charged_currencies
from mpt_usage_reporting_extension.services.estimates_uploader import (
    EstimatesUploader,
    EstimateUploadReport,
    updatable_subscription_ids,
)
from mpt_usage_reporting_extension.services.execution_notifier import ExecutionSummary
from mpt_usage_reporting_extension.services.execution_tracker import (
    Execution,
    ExecutionTracker,
    StatementProcessingRecorder,
)
from mpt_usage_reporting_extension.services.statements import StatementReport, StatementSelector
from mpt_usage_reporting_extension.steps import logged_step
from mpt_usage_reporting_extension.types import Command, Month
from mpt_usage_reporting_extension.utils import last_month, sanitize_id, scope_label
from mpt_usage_reporting_extension.window import RunWindow

logger = logging.getLogger(__name__)


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

    @trace_span(
        "usage_reporting.run",
        attributes={
            "usage_reporting.window.start": lambda pipeline, parameters: (
                pipeline._ctx.window.start.isoformat()  # noqa: SLF001, WPS437
            ),
            "usage_reporting.window.end": lambda pipeline, parameters: (
                pipeline._ctx.window.end.isoformat()  # noqa: SLF001, WPS437
            ),
            "usage_reporting.product_ids": lambda pipeline, parameters: ",".join(
                pipeline._ctx.product_ids  # noqa: SLF001, WPS437
            ),
        },
    )
    @logged_step("run")
    async def run(self, parameters: Mapping[str, object]) -> None:
        """Collect charges, persist them, update estimates, then prune old rows."""
        self._log_inputs(Command.RUN)
        await self._tracked(Command.RUN, parameters, self._run)

    @trace_span(
        "usage_reporting.recalculate",
        attributes={
            "usage_reporting.dry_run": lambda pipeline, scope, parameters, **kwargs: kwargs.get(
                "dry_run", False
            ),
            "usage_reporting.scope": lambda pipeline, scope, parameters, **kwargs: (
                type(scope).__name__
            ),
        },
    )
    @logged_step("recalculate")
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
        self._log_inputs(Command.RECALCULATE, scope, dry_run=dry_run)
        await self._tracked(
            Command.RECALCULATE,
            parameters,
            functools.partial(self._reset_and_refill, scope, dry_run=dry_run),
        )

    @trace_span("usage_reporting.plan_reset")
    @logged_step("plan_reset")
    async def plan_reset(self, scope: Selector | None, db: Database) -> ResetScope:
        """Resolve the scope a recalculate rebuilds, reading the store and the API only.

        A ``None`` scope is expanded to the run's configured products, so the rebuild matches the
        re-fill's product scope instead of touching buckets of unrelated products; the per-product
        scopes are unioned into one. A subscription scope keeps its shared agreement buckets, so
        its ``agreement_ids`` (the agreement buckets to rebuild) are empty.
        """
        deleter = self._deleter(db, dry_run=True)
        if scope is None:
            return await self._plan_products(deleter)
        resolved = await deleter.resolve(scope)
        rebuilt = frozenset() if isinstance(scope, SubscriptionSelector) else resolved.agreements
        return ResetScope(resolved.subscriptions, resolved.agreements, rebuilt)

    @trace_span("usage_reporting.reset")
    @logged_step("reset")
    async def delete_planned(self, planned: ResetScope, db: Database, *, dry_run: bool) -> None:
        """Delete exactly the planned scope's buckets, the ones the accumulation rebuilds.

        A dry run only reports what it would delete.
        """
        await self._deleter(db, dry_run=dry_run).delete_resolved(
            planned.subscriptions, planned.agreement_ids
        )

    @trace_span("usage_reporting.select_statements")
    @logged_step("select_statements")
    async def select_statements(self, agreement_ids: tuple[str, ...] = ()) -> list[Statement]:
        """Select the run window's statements and render the statement report.

        When ``agreement_ids`` is given the selection is narrowed to those agreements; otherwise it
        uses the run's configured products (and optional seller).
        """
        statements = await StatementSelector(self._ctx.api_service).select(
            self._ctx.window, self._ctx.product_ids, self._ctx.seller_id, agreement_ids
        )
        StatementReport(statements, self._ctx.window).render()
        return statements

    @trace_span(
        "usage_reporting.accumulate_charges",
        attributes={
            "usage_reporting.statement_count": lambda pipeline, statements, recorder, **kwargs: len(
                statements
            ),
        },
    )
    @logged_step("accumulate_charges")
    async def accumulate_charges(
        self,
        statements: list[Statement],
        recorder: StatementProcessingRecorder,
        *,
        isolate_agreements: bool = False,
    ) -> ChargeTotals:
        """Stream and accumulate the statements' charges, then render the charge report.

        With ``isolate_agreements`` an agreement with a charge that cannot be summed is left out
        and listed in the totals' ``failed_agreements``; otherwise the command fails.

        Raises:
            ChargePriceError: a charge carries no price the accumulation can sum, unless
                ``isolate_agreements``; the command fails before anything is persisted.
        """
        accumulator = ChargeAccumulator(ChargeStreamer(self._ctx.api_service), recorder)
        totals = await accumulator.accumulate(
            statements, self._ctx.charge_filter, isolate_agreements=isolate_agreements
        )
        ChargeReport(totals).render()
        return totals

    @trace_span("usage_reporting.persist")
    @logged_step("persist")
    async def persist(
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

    @trace_span("usage_reporting.update_estimates")
    @logged_step("update_estimates")
    async def update_estimates(
        self,
        accumulations: list[ChargeAccumulation],
        subscription_repo: SubscriptionAccumulationRepository,
        *,
        dry_run: bool,
    ) -> EstimateUploadReport:
        """Upload estimates for the run's subscriptions and return the upload report.

        The purchase currencies of the run's charges arm the uploader's currency guard, so an
        estimate summed over more than one currency is never uploaded.
        The caller inspects the report's failures; this method no longer exits, so the execution
        row can be finalised before the process exits non-zero.
        """
        anchor = last_month(dt.datetime.now(tz=dt.UTC).date())
        api_service = self._ctx.api_service
        report: EstimateUploadReport = await EstimatesUploader(
            subscription_repo,
            api_service.subscriptions,
            dry_run=dry_run,
        ).update(
            updatable_subscription_ids(accumulations),
            anchor.year,
            Month(anchor.month),
            purchase_currencies=charged_currencies(accumulations),
        )
        report.render()
        return report

    @trace_span("usage_reporting.prune")
    @logged_step("cleanup")
    async def cleanup(
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

    def _log_inputs(
        self, command: Command, scope: Selector | None = None, *, dry_run: bool = False
    ) -> None:
        """Log the resolved inputs a command runs with, before any work starts."""
        logger.info(
            "Running %s window=%s products=%s seller=%s scope=%s dry_run=%s",
            command,
            self._window_label(self._ctx.window),
            ",".join(self._ctx.product_ids) or "-",
            sanitize_id(self._ctx.seller_id) or "-",
            scope_label(scope),
            dry_run,
        )
        if dry_run:
            logger.info("Dry run: read-only mode, no writes or estimate updates will be made.")

    def _window_label(self, window: RunWindow | None) -> str:
        """Render the run window for the inputs line; a ``None`` window bounds nothing."""
        if window is None:
            return "every date"
        start = window.start.date().isoformat()
        end = window.end.date().isoformat()
        return f"{start}..{end}"

    async def _reset_and_refill(
        self,
        scope: Selector | None,
        db: Database,
        execution: Execution,
        *,
        dry_run: bool,
    ) -> None:
        """Accumulate the scope's charges first, then delete and re-fill its buckets.

        The scope is resolved by reading only (``plan_reset``), and its statements' charges are
        accumulated before anything is deleted. A failure while accumulating therefore leaves the
        stored buckets and the pushed estimates untouched, and a re-run starts from the same state.
        An agreement with a charge that cannot be summed (for example one without ``BSPx1``) fails
        alone: it is taken out of the scope, so its buckets and estimates stay as they were, the
        rest of the scope is rebuilt, and the execution finishes with errors naming it. Only once
        accumulation is done is the scope deleted -
        exactly the subscriptions and agreements it resolved, not a fresh resolution - and
        re-filled.

        Only a subscription scope narrows the charge stream to its subscription: its statements
        belong to agreements shared with sibling subscriptions whose buckets stay intact. Every
        other scope rebuilds whole agreements, so its charges are kept by agreement afterwards
        (``_filter_to_reset``); filtering them by the resolved subscription ids would drop
        agreement-level charges (no subscription id) and charges of subscriptions with no stored
        bucket yet, rebuilding those agreements short.
        """
        planned = await self.plan_reset(scope, db)
        totals, planned = await self._accumulate_planned(scope, planned, db, execution)
        kept = self._filter_to_reset(totals.accumulations.values(), planned)
        await self.delete_planned(planned, db, dry_run=dry_run)
        await self._refill(kept, planned, db, execution, dry_run=dry_run)

    async def _accumulate_planned(
        self, scope: Selector | None, planned: ResetScope, db: Database, execution: Execution
    ) -> tuple[ChargeTotals, ResetScope]:
        """Accumulate the planned scope's statements and take the failed agreements out of it."""
        recorder = StatementProcessingRecorder(db.statement_processing_repository(), execution.id)
        with self._scoped_charge_filter(self._narrowed_subscriptions(scope, planned)):
            statements = await self.select_statements(tuple(sorted(planned.statement_agreements)))
            totals = await self.accumulate_charges(statements, recorder, isolate_agreements=True)
        execution.record_result(statements=len(statements))
        failed = frozenset(totals.failed_agreements)
        if failed:
            execution.record_result(failed_agreements=", ".join(sorted(failed)))
            execution.has_errors = True
            planned = await self._without_agreements(planned, failed, db)
        return totals, planned

    async def _tracked(
        self,
        command: Command,
        parameters: Mapping[str, object],
        body: Callable[[Database, Execution], Awaitable[None]],
    ) -> None:
        """Track the command's execution and notify Teams of its outcome.

        Every outcome is reported: success (with the execution result as the run report),
        completed-with-errors (as a failure, keeping the non-zero exit), and unhandled
        exceptions (as a failure with the stacktrace, re-raised afterwards).
        """
        started_at = dt.datetime.now(tz=dt.UTC)
        try:
            execution = await self._track(command, parameters, body)
        except Exception as exc:
            await self._ctx.notifier.notify_failure(
                self._finished_execution(command, started_at), str(exc), traceback.format_exc()
            )
            raise
        summary = self._finished_execution(command, started_at)
        if execution.has_errors:
            await self._ctx.notifier.notify_failure(summary, self._errors_summary(execution.result))
            raise typer.Exit(code=1)
        await self._ctx.notifier.notify_success(summary, execution.result)

    async def _track(
        self,
        command: Command,
        parameters: Mapping[str, object],
        body: Callable[[Database, Execution], Awaitable[None]],
    ) -> Execution:
        """Run the command body inside a fresh DB and a tracked execution row."""
        async with PostgresDatabase(resolve_database_url()) as db:
            tracker = ExecutionTracker(db.execution_repository())
            async with tracker.track(command, parameters) as execution:
                await body(db, execution)
                return execution

    def _finished_execution(self, command: Command, started_at: dt.datetime) -> ExecutionSummary:
        """Snapshot the just-finished execution, measuring its duration up to now."""
        return ExecutionSummary(
            name=command.value,
            command=" ".join(sys.argv),
            started_at=started_at,
            duration=dt.datetime.now(tz=dt.UTC) - started_at,
        )

    def _errors_summary(self, report: Mapping[str, object]) -> str:
        entries = [f"{name}={count}" for name, count in report.items()]
        rendered = ", ".join(entries)
        return f"Command completed with errors ({rendered})"

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
        statements = await self.select_statements()
        totals = await self.accumulate_charges(statements, recorder)
        accumulations = list(totals.accumulations.values())
        await self.persist(accumulations, db.subscription_repository(), db.agreement_repository())
        report = await self.update_estimates(
            accumulations, db.subscription_repository(), dry_run=False
        )
        await self.cleanup(db.subscription_repository(), db.agreement_repository())
        execution.record_result(
            statements=len(statements),
            accumulations=len(accumulations),
            estimates_failed=report.failed_count,
        )
        if report.has_failures:
            execution.has_errors = True

    async def _refill(
        self,
        kept: list[ChargeAccumulation],
        reset_scope: ResetScope,
        db: Database,
        execution: Execution,
        *,
        dry_run: bool,
    ) -> None:
        """Persist the reset scope's accumulated buckets, push their estimates, then prune."""
        await self.persist(
            kept,
            db.subscription_repository(),
            db.agreement_repository(),
            agreement_ids=reset_scope.agreement_ids,
            dry_run=dry_run,
        )
        report = await self.update_estimates(kept, db.subscription_repository(), dry_run=dry_run)
        await self.cleanup(
            db.subscription_repository(),
            db.agreement_repository(),
            dry_run=dry_run,
        )
        execution.record_result(accumulations=len(kept), estimates_failed=report.failed_count)
        if report.has_failures:
            execution.has_errors = True

    async def _plan_products(self, deleter: BucketDeleter) -> ResetScope:
        """Resolve each configured product's scope and union them."""
        subscriptions: set[str] = set()
        agreements: set[str] = set()
        for product_id in self._ctx.product_ids:
            resolved = await deleter.resolve(ProductSelector(product_id))  # noqa: WPS476
            subscriptions |= resolved.subscriptions
            agreements |= resolved.agreements
        rebuilt = frozenset(agreements)
        return ResetScope(frozenset(subscriptions), rebuilt, rebuilt)

    def _deleter(self, db: Database, *, dry_run: bool) -> BucketDeleter:
        """Build the bucket deleter over the database's repositories."""
        api_service = self._ctx.api_service
        return BucketDeleter(
            db.subscription_repository(),
            db.agreement_repository(),
            api_service.client.commerce.subscriptions,
            dry_run=dry_run,
        )

    def _narrowed_subscriptions(self, scope: Selector | None, reset: ResetScope) -> frozenset[str]:
        """The subscriptions to narrow the charge stream to: only a subscription scope narrows."""
        return reset.subscriptions if isinstance(scope, SubscriptionSelector) else frozenset()

    async def _without_agreements(
        self, planned: ResetScope, agreement_ids: frozenset[str], db: Database
    ) -> ResetScope:
        """Take the agreements and their stored subscriptions out of the planned scope."""
        resolved = await self._deleter(db, dry_run=True).resolve_agreements(set(agreement_ids))
        return ResetScope(
            planned.subscriptions - resolved.subscriptions,
            planned.statement_agreements - agreement_ids,
            planned.agreement_ids - agreement_ids,
        )

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
