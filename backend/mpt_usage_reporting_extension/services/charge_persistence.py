import logging

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
    ) -> None:
        self._subscription_repo = subscription_repo
        self._agreement_repo = agreement_repo

    async def persist(self, accumulations: list[ChargeAccumulation]) -> None:
        """Additively upsert each accumulation bucket into both monthly tables.

        Buckets without a billing month are skipped to respect the table CHECK constraints.
        """
        for bucket in accumulations:
            await self._write(bucket)  # noqa: WPS476

    async def _write(self, bucket: ChargeAccumulation) -> None:
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
        await self._subscription_repo.accumulate(charge)
        await self._agreement_repo.accumulate(charge)
