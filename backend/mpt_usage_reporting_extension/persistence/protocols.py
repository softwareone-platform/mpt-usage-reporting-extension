from typing import Protocol

from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    Charge,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.types import Month, Year


class SubscriptionAccumulationRepository(Protocol):
    """Persist and read monthly accumulation totals per subscription bucket."""

    def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the subscription bucket."""
        ...

    def get(
        self,
        *,
        subscription_id: str,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        ...


class AgreementAccumulationRepository(Protocol):
    """Persist and read monthly accumulation totals per agreement bucket."""

    def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the agreement bucket."""
        ...

    def get(
        self,
        *,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> AgreementMonthlyAccumulation | None:
        """Return the stored agreement bucket, or None when absent."""
        ...
