from dataclasses import dataclass, field
from decimal import Decimal

from mpt_usage_reporting_extension.types import Month, Year

#: Bucket key ordered as agreement id, subscription id, year, then month.
AccumulationKey = tuple[str, str, Year | None, Month | None]


@dataclass
class ChargeAccumulation:
    """Accumulated price totals for one (agreement, subscription, year, month) bucket."""

    agreement_id: str
    subscription_id: str
    year: Year | None
    month: Month | None
    ppx1: Decimal = Decimal(0)
    spx1: Decimal = Decimal(0)


@dataclass
class ChargeTotals:
    """Aggregate charge totals for a run, grouped per accumulation key."""

    charge_count: int = 0
    accumulations: dict[AccumulationKey, ChargeAccumulation] = field(default_factory=dict)
