import datetime as dt
from collections.abc import AsyncIterator
from typing import Protocol

from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    Charge,
    PriceEstimate,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.types import Month, Year


class SubscriptionAccumulationRepository(Protocol):
    """Persist and read monthly accumulation totals per subscription bucket."""

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the subscription bucket."""
        ...

    async def get(
        self,
        *,
        subscription_id: str,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> SubscriptionMonthlyAccumulation | None:
        """Return the stored subscription bucket, or None when absent."""
        ...

    async def estimate(self, subscription_id: str, year: Year, month: Month) -> PriceEstimate:
        """Current-month (PPxM/SPxM) and trailing-12-month (PPxY/SPxY) sums for the subscription."""
        ...

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        ...

    def updated(self, updated_on: dt.date) -> AsyncIterator[SubscriptionMonthlyAccumulation]:
        """Yield the subscription buckets last written on updated_on (streamed)."""
        ...


class AgreementAccumulationRepository(Protocol):
    """Persist and read monthly accumulation totals per agreement bucket."""

    async def accumulate(self, charge: Charge) -> None:
        """Additively accumulate the charge into the agreement bucket."""
        ...

    async def get(
        self,
        *,
        agreement_id: str,
        year: Year,
        month: Month,
    ) -> AgreementMonthlyAccumulation | None:
        """Return the stored agreement bucket, or None when absent."""
        ...

    async def estimate(self, agreement_id: str, year: Year, month: Month) -> PriceEstimate:
        """Current-month (PPxM/SPxM) and trailing-12-month (PPxY/SPxY) sums for the agreement."""
        ...

    async def prune(self, year: Year, month: Month) -> int:
        """Delete buckets older than the 18-month retention window ending at (year, month)."""
        ...

    def updated(self, updated_on: dt.date) -> AsyncIterator[AgreementMonthlyAccumulation]:
        """Yield the agreement buckets last written on updated_on (streamed)."""
        ...
