import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from mpt_extension_sdk.observability import trace_span

from mpt_usage_reporting_extension.persistence.postgres.database import (
    PostgresDatabase,
    resolve_database_url,
)
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.services.execution_tracker import ExecutionTracker
from mpt_usage_reporting_extension.types import Command, Month, Year

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """How many rows each accumulation table shed for the retention window ending (year, month)."""

    year: Year
    month: Month
    subscription_deleted: int
    agreement_deleted: int


class CleanupReport:
    """Render the outcome of a retention cleanup as a one-line summary."""

    def __init__(self, outcome: CleanupOutcome, *, dry_run: bool = False) -> None:
        self._outcome = outcome
        self._dry_run = dry_run

    def render(self) -> None:
        """Log the summary line for the cleanup."""
        logger.info(self._summary())

    def _summary(self) -> str:
        window = self._window_label()
        if self._dry_run:
            # The prune never ran, so there is no count to report - claiming 0 would read
            # as "nothing to prune" for a window that may hold thousands of rows.
            return f"Would prune the subscription and agreement rows {window} (count not measured)"
        subscription = self._outcome.subscription_deleted
        agreement = self._outcome.agreement_deleted
        return f"Pruned {subscription} subscription and {agreement} agreement row(s) {window}"

    def _window_label(self) -> str:
        year = self._outcome.year
        month = str(self._outcome.month).zfill(2)
        return f"older than the rolling 18-month window ending {year}-{month}"


class AccumulationCleaner:
    """Delete accumulation rows older than the rolling 18-month retention window."""

    def __init__(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
        *,
        dry_run: bool = False,
    ) -> None:
        self._subscription_repo = subscription_repo
        self._agreement_repo = agreement_repo
        self._dry_run = dry_run

    async def cleanup(self, year: Year, month: Month) -> CleanupOutcome:
        """Prune both tables to the 18-month window ending at (year, month), then report."""
        if self._dry_run:
            subscription_deleted = 0
            agreement_deleted = 0
        else:
            subscription_deleted = await self._subscription_repo.prune(year, month)
            agreement_deleted = await self._agreement_repo.prune(year, month)
        outcome = CleanupOutcome(year, month, subscription_deleted, agreement_deleted)
        CleanupReport(outcome, dry_run=self._dry_run).render()
        return outcome


@trace_span(
    "usage_reporting.cleanup",
    attributes={"usage_reporting.anchor": lambda anchor, parameters: anchor.isoformat()},
)
async def do_cleanup(anchor: dt.date, parameters: Mapping[str, object]) -> CleanupOutcome:
    """Open the store and prune both tables to the 18-month window ending at the anchor month."""
    async with PostgresDatabase(resolve_database_url()) as db:
        tracker = ExecutionTracker(db.execution_repository())
        async with tracker.track(Command.CLEANUP, parameters) as execution:
            outcome = await AccumulationCleaner(
                db.subscription_repository(), db.agreement_repository()
            ).cleanup(anchor.year, Month(anchor.month))
            execution.record_result(
                subscription_deleted=outcome.subscription_deleted,
                agreement_deleted=outcome.agreement_deleted,
            )
        return outcome
