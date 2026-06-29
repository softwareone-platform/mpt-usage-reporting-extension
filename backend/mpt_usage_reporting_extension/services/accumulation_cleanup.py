import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass

import typer

from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
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

    def __init__(self, outcome: CleanupOutcome) -> None:
        self._outcome = outcome

    def render(self) -> None:
        """Print the summary line for the cleanup."""
        typer.echo(self._summary())

    def _summary(self) -> str:
        subscription = self._outcome.subscription_deleted
        agreement = self._outcome.agreement_deleted
        year = self._outcome.year
        month = str(self._outcome.month).zfill(2)
        return (
            f"Pruned {subscription} subscription and {agreement} agreement row(s) "
            f"older than the rolling 18-month window ending {year}-{month}"
        )


class AccumulationCleaner:
    """Delete accumulation rows older than the rolling 18-month retention window."""

    def __init__(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
    ) -> None:
        self._subscription_repo = subscription_repo
        self._agreement_repo = agreement_repo

    async def cleanup(self, year: Year, month: Month) -> CleanupOutcome:
        """Prune both tables to the 18-month window ending at (year, month), then report."""
        subscription_deleted = await self._subscription_repo.prune(year, month)
        agreement_deleted = await self._agreement_repo.prune(year, month)
        outcome = CleanupOutcome(year, month, subscription_deleted, agreement_deleted)
        logger.info(
            "Pruned %d subscription and %d agreement accumulation row(s) older than %d-%02d",
            subscription_deleted,
            agreement_deleted,
            year,
            month,
        )
        CleanupReport(outcome).render()
        return outcome


async def do_cleanup(anchor: dt.date, parameters: Mapping[str, object]) -> CleanupOutcome:
    """Open the store and prune both tables to the 18-month window ending at the anchor month."""
    async with SqliteDatabase(resolve_db_path()) as db:
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
