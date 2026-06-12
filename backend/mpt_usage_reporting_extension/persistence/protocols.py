import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Protocol

from mpt_usage_reporting_extension.persistence.models import (
    AgreementMonthlyAccumulation,
    Charge,
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

    async def monthly_estimate(self, subscription_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 for the subscription's bucket in the single (year, month)."""
        ...

    async def yearly_estimate(self, subscription_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 across the subscription's trailing 12 months ending at (year, month)."""
        ...

    def updated(self, updated_on: dt.date) -> AsyncIterator[Charge]:
        """Yield the subscription buckets last written on updated_on, as Charges (streamed)."""
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

    async def monthly_estimate(self, agreement_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 for the agreement's bucket in the single (year, month)."""
        ...

    async def yearly_estimate(self, agreement_id: str, year: Year, month: Month) -> Decimal:
        """Sum ppx1 across the agreement's trailing 12 months ending at (year, month)."""
        ...

    def updated(self, updated_on: dt.date) -> AsyncIterator[Charge]:
        """Yield the agreement buckets last written on updated_on, as Charges (streamed)."""
        ...
