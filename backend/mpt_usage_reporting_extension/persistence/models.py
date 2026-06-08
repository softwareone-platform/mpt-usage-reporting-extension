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
class AgreementMonthlyAccumulation:
    """Accumulated monthly usage totals for a single agreement bucket."""

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
