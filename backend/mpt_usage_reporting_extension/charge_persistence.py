import logging

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)

logger = logging.getLogger(__name__)


class ChargePersister:
    """Upsert each accumulated monthly bucket into both accumulation tables."""

    def persist(
        self,
        ctx: RunContext,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
    ) -> None:
        """Iterate the run's accumulation buckets and additively upsert each one.

        Reads the accumulated :class:`ChargeTotals` from ``ctx`` and writes every bucket
        to both the subscription and agreement monthly accumulation tables. Buckets
        without a billing month are skipped to respect the table CHECK constraints.
        """
        totals = ctx.charge_totals
        if totals is None:
            return
        for bucket in totals.accumulations.values():
            self._write(bucket, subscription_repo, agreement_repo)

    def _write(
        self,
        bucket: ChargeAccumulation,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
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
        subscription_repo.accumulate(charge)
        agreement_repo.accumulate(charge)
