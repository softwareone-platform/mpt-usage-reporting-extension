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
class AccumulationPeriod:
    """One accumulated month for a subscription, with its totals and freshest write time."""

    subscription_id: str
    year: Year
    month: Month
    ppx1: Decimal
    spx1: Decimal
    updated_at: str

    def to_payload(self) -> dict[str, object]:
        """Return the period as the API's camelCase payload."""
        return {
            "subscriptionId": self.subscription_id,
            "year": int(self.year),
            "month": int(self.month),
            "ppx1": float(self.ppx1),
            "spx1": float(self.spx1),
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """One recorded command execution, as read back for the status report."""

    command: str
    status: str
    started_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class ExecutionDetail:
    """One command execution row with its identity, inputs, and outcome."""

    id: int
    command: str
    status: str
    parameters: dict[str, object]
    result: dict[str, object] | None
    started_at: str
    completed_at: str | None

    def to_payload(self) -> dict[str, object]:
        """Return the execution as the API's camelCase payload."""
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "parameters": self.parameters,
            "result": self.result,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class PriceEstimate:
    """Current-month (PPxM/SPxM) and trailing-year (PPxY/SPxY) purchase/sales sums."""

    ppxm: Decimal
    spxm: Decimal
    ppxy: Decimal
    spxy: Decimal

    def to_dict(self) -> dict[str, float]:
        """Return the estimate as the API price payload (PPxM/SPxM/PPxY/SPxY)."""
        return {
            "PPxM": float(self.ppxm),
            "SPxM": float(self.spxm),
            "PPxY": float(self.ppxy),
            "SPxY": float(self.spxy),
        }
