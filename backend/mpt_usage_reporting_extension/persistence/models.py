"""Frozen row models for the accumulation tables."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SubscriptionMonthlyAccumulation:
    """Accumulated monthly usage totals for a single subscription bucket."""

    subscription_id: str
    agreement_id: str
    year: int
    month: int
    ppx1: Decimal
    spx1: Decimal
    updated_at: dt.datetime


@dataclass(frozen=True, slots=True)
class AgreementMonthlyAccumulation:
    """Accumulated monthly usage totals for a single agreement bucket."""

    agreement_id: str
    year: int
    month: int
    ppx1: Decimal
    spx1: Decimal
    updated_at: dt.datetime
