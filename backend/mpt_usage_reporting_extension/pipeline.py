import contextlib
import datetime as dt
import functools
import sys
import traceback
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass

import typer
from mpt_api_client.resources.billing.statements import Statement
from mpt_extension_sdk.observability import trace_span

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
from mpt_usage_reporting_extension.services.bucket_delete import BucketDeleter, ScopeBucketDeleter
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
from mpt_usage_reporting_extension.services.execution_notifier import ExecutionSummary
from mpt_usage_reporting_extension.services.execution_tracker import (
    Execution,
    ExecutionTracker,
    StatementProcessingRecorder,
)
from mpt_usage_reporting_extension.services.scope_resolver import ScopeResolver
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
    async def run(self, parameters: Mapping[str, object]) -> None:
        """Collect charges, persist them, update estimates, then prune old rows."""
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
        if dry_run:
            typer.echo("Dry run: running recalculate in read-only mode (no writes or updates).")

        await self._tracked(
            Command.RECALCULATE,
            parameters,
            functools.partial(self._reset_and_refill, scope, dry_run=dry_run),
        )

    @trace_span("usage_reporting.reset")
    async def reset(
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
        resolver = ScopeResolver(
            api_service.client.commerce.subscriptions, db.subscription_repository()
        )
        deleter = ScopeBucketDeleter(
            BucketDeleter(
                db.subscription_repository(),
                db.agreement_repository(),
                resolver,
                dry_run=dry_run,
            ),
            resolver,
        )
        if scope is not None:
            reset = await deleter.delete(scope)
            statement_agreements = reset.statement_agreements
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

    @trace_span("usage_reporting.select_statements")
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
            "usage_reporting.statement_count": lambda pipeline, statements, recorder: len(
                statements
            ),
        },
    )
    async def accumulate_charges(
        self, statements: list[Statement], recorder: StatementProcessingRecorder
    ) -> Iterable[ChargeAccumulation]:
        """Stream and accumulate the statements' charges, then render the charge report."""
        totals = await ChargeAccumulator().accumulate(
            ChargeStreamer(self._ctx.api_service, recorder).stream(statements),
            self._ctx.charge_filter,
        )
        ChargeReport(totals).render()
        return totals.accumulations.values()

    @trace_span("usage_reporting.persist")
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
    async def update_estimates(
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
        report: EstimateUploadReport = await EstimatesUploader(
            subscription_repo,
            subscriptions,  # type: ignore[arg-type]
            dry_run=dry_run,
        ).update(updatable_subscription_ids(accumulations), anchor.year, Month(anchor.month))
        report.render()
        return report

    @trace_span("usage_reporting.prune")
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

    async def _reset_and_refill(
        self,
        scope: Selector | None,
        db: Database,
        execution: Execution,
        *,
        dry_run: bool,
    ) -> None:
        """Delete the scope's buckets, then re-fill exactly what the reset removed."""
        reset_scope = await self.reset(scope, db, dry_run=dry_run)
        with self._scoped_charge_filter(reset_scope.subscriptions):
            await self._refill(reset_scope, db, execution, dry_run=dry_run)

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
        accumulations = list(await self.accumulate_charges(statements, recorder))
        await self.persist(accumulations, db.subscription_repository(), db.agreement_repository())
        report = await self.update_estimates(
            accumulations,
            db.subscription_repository(),
            subscriptions=self._ctx.api_service.subscriptions,
            dry_run=False,
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
        reset_scope: ResetScope,
        db: Database,
        execution: Execution,
        *,
        dry_run: bool,
    ) -> None:
        """Re-accumulate the selector-defined reset scope's buckets, then prune old rows."""
        recorder = StatementProcessingRecorder(db.statement_processing_repository(), execution.id)
        statements = await self.select_statements(tuple(sorted(reset_scope.statement_agreements)))
        accumulations = await self.accumulate_charges(statements, recorder)
        kept = self._filter_to_reset(accumulations, reset_scope)
        await self.persist(
            kept,
            db.subscription_repository(),
            db.agreement_repository(),
            agreement_ids=reset_scope.agreement_ids,
            dry_run=dry_run,
        )
        report = await self.update_estimates(
            kept,
            db.subscription_repository(),
            subscriptions=self._ctx.api_service.subscriptions,
            dry_run=dry_run,
        )
        await self.cleanup(
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

    async def _reset_products(self, deleter: ScopeBucketDeleter) -> ResetScope:
        """Delete each configured product's buckets and union their reset scopes."""
        subscriptions: set[str] = set()
        statement_agreements: set[str] = set()
        for product_id in self._ctx.product_ids:
            outcome = await deleter.delete(ProductSelector(product_id))  # noqa: WPS476
            subscriptions |= set(outcome.subscriptions)
            statement_agreements |= outcome.statement_agreements
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
