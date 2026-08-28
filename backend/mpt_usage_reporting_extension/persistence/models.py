import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from mpt_usage_reporting_extension.types import Month, Year


@dataclass(frozen=True, slots=True)
class SubscriptionMonthlyAccumulation:
    """Accumulated monthly usage totals for a single subscription bucket."""

    subscription_id: str
    agreement_id: str
    year: Year
    month: Month
    ppx1: Decimal
    spx1: Decimal
    updated_at: dt.datetime


@dataclass(frozen=True, slots=True)
class Charge:
    """A single charge to accumulate into the monthly buckets."""

    subscription_id: str
    agreement_id: str
    year: Year
    month: Month
    ppx1: Decimal
    spx1: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """One recorded command execution, as read back for the status report."""

    command: str
    status: str
    started_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class PriceEstimate:
    """Current-month (PPxM/SPxM) and trailing-year (PPxY/SPxY) purchase/sales sums.

    ``None`` means no billing data backs that figure and is uploaded as JSON null.
    """

    ppxm: Decimal | None
    spxm: Decimal | None
    ppxy: Decimal | None
    spxy: Decimal | None

    def to_dict(self) -> dict[str, float | None]:
        """Return the estimate as the API price payload (PPxM/SPxM/PPxY/SPxY)."""
        return {
            "PPxM": self._as_float(self.ppxm),
            "SPxM": self._as_float(self.spxm),
            "PPxY": self._as_float(self.ppxy),
            "SPxY": self._as_float(self.spxy),
        }

    def to_sales_dict(self) -> dict[str, float | None]:
        """Return only the sales prices (SPxM/SPxY); the platform recalculates the rest."""
        return {
            "SPxM": self._as_float(self.spxm),
            "SPxY": self._as_float(self.spxy),
        }

    @staticmethod
    def _as_float(amount: Decimal | None) -> float | None:  # noqa: WPS602
        """Render one price as the API payload value, keeping absent figures null."""
        return None if amount is None else float(amount)
