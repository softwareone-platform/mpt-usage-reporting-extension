import logging
from collections.abc import Iterable
from dataclasses import dataclass

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.utils import sanitize_log_value

logger = logging.getLogger(__name__)


@dataclass
class PersistOutcome:
    """How many buckets a persist run wrote to each table, and how many it skipped."""

    subscriptions: int = 0
    agreements: int = 0
    skipped: int = 0


class PersistReport:
    """Render the outcome of a persist run as a one-line summary."""

    def __init__(self, outcome: PersistOutcome, *, dry_run: bool = False) -> None:
        self._outcome = outcome
        self._dry_run = dry_run

    def render(self) -> None:
        """Log the summary line for the persist run."""
        logger.info(self._summary())

    def _summary(self) -> str:
        verb = "Would accumulate into" if self._dry_run else "Accumulated into"
        subscriptions = self._outcome.subscriptions
        agreements = self._outcome.agreements
        skipped = self._outcome.skipped
        return (
            f"{verb} {subscriptions} subscription and {agreements} agreement "
            f"bucket(s), skipped {skipped} without a storable billing month"
        )


class AccumulationPersister:
    """Upsert each accumulated monthly bucket into both accumulation tables."""

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

    async def persist(
        self,
        accumulations: Iterable[ChargeAccumulation],
        agreement_ids: frozenset[str] | None = None,
    ) -> None:
        """Additively upsert each accumulation bucket into the monthly tables.

        Buckets without a billing month are skipped to respect the table CHECK constraints.

        ``agreement_ids`` controls the agreement table. ``None`` (the default, used by a regular
        run) writes every bucket's agreement total. A set restricts agreement writes to those,
        so a subscription-scoped recalculate (empty set) rebuilds its subscription bucket without
        touching the shared agreement bucket it left intact.
        """
        outcome = PersistOutcome()
        for bucket in accumulations:
            await self._write(bucket, agreement_ids, outcome)  # noqa: WPS476
        PersistReport(outcome, dry_run=self._dry_run).render()

    async def _write(
        self,
        bucket: ChargeAccumulation,
        agreement_ids: frozenset[str] | None,
        outcome: PersistOutcome,
    ) -> None:
        period = bucket.storable_period()
        if period is None:
            outcome.skipped += 1
            logger.warning(
                "Skipping persistence for bucket without a storable billing month "
                "(agreement=%s, subscription=%s, year=%s, month=%s)",
                sanitize_log_value(bucket.agreement_id),
                sanitize_log_value(bucket.subscription_id),
                bucket.year,
                bucket.month,
            )
            return
        year, month = period
        charge = Charge(
            subscription_id=bucket.subscription_id,
            agreement_id=bucket.agreement_id,
            year=year,
            month=month,
            ppx1=bucket.ppx1,
            spx1=bucket.spx1,
        )
        writes_agreement = agreement_ids is None or bucket.agreement_id in agreement_ids
        logger.info(
            "Adding to bucket subscription=%s agreement=%s period=%d-%02d "
            "add_ppx1=%s add_spx1=%s agreement_table=%s dry_run=%s",
            sanitize_log_value(bucket.subscription_id),
            sanitize_log_value(bucket.agreement_id),
            bucket.year,
            bucket.month,
            bucket.ppx1,
            bucket.spx1,
            writes_agreement,
            self._dry_run,
        )
        outcome.subscriptions += 1
        if writes_agreement:
            outcome.agreements += 1
        if self._dry_run:
            return
        await self._subscription_repo.accumulate(charge)
        if writes_agreement:
            await self._agreement_repo.accumulate(charge)
