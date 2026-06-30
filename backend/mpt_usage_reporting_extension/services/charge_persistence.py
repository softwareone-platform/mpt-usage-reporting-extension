import logging
from collections.abc import Iterable

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)

logger = logging.getLogger(__name__)


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
        for bucket in accumulations:
            await self._write(bucket, agreement_ids)  # noqa: WPS476

    async def _write(
        self, bucket: ChargeAccumulation, agreement_ids: frozenset[str] | None
    ) -> None:
        if bucket.year is None or bucket.month is None:
            logger.warning(
                "Skipping persistence for bucket without a billing month "
                "(agreement=%s, subscription=%s)",
                bucket.agreement_id,
                bucket.subscription_id,
            )
            return
        charge = Charge(
            subscription_id=bucket.subscription_id,
            agreement_id=bucket.agreement_id,
            year=bucket.year,
            month=bucket.month,
            ppx1=bucket.ppx1,
            spx1=bucket.spx1,
        )
        if self._dry_run:
            return
        await self._subscription_repo.accumulate(charge)
        if agreement_ids is None or bucket.agreement_id in agreement_ids:
            await self._agreement_repo.accumulate(charge)
