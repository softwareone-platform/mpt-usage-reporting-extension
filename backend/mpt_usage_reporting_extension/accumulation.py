import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple, Self

from mpt_api_client.resources.billing.statement_charges import StatementCharge

from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.types import Month, Year, is_storable_year

_AGREEMENT_ID = "agreement.id"
_SUBSCRIPTION_ID = "subscription.id"
_CHARGE_PERIOD_END = "period.end"
_STATEMENT_CANCELLED_AT = "statement.audit.cancelled.at"
_STATEMENT_ISSUED_AT = "statement.audit.issued.at"
_UNKNOWN_ID = "-"
_UNKNOWN_YEAR_MONTH: tuple[None, None] = (None, None)
_DATE_PATHS = (_CHARGE_PERIOD_END, _STATEMENT_CANCELLED_AT, _STATEMENT_ISSUED_AT)


def read_path(charge: StatementCharge, path: str) -> str | None:
    """Walk a dot-notated attribute path, returning None when any segment is missing."""
    current: Any = charge
    for attr in path.split("."):
        current = getattr(current, attr, None)
        if current is None:
            return None
    return str(current)


class StatementChargeFilter:
    """Keep only charges whose subscription id is among the selected ids."""

    def __init__(self, subscription_ids: Iterable[str]) -> None:
        self.subscription_ids = frozenset(subscription_ids)

    @classmethod
    def for_subscriptions(cls, subscription_ids: Iterable[str]) -> "StatementChargeFilter | None":
        """Build a filter for the given ids, or None when there is nothing to filter by."""
        ids = frozenset(subscription_ids)
        return cls(ids) if ids else None

    def matches(self, charge: StatementCharge) -> bool:
        """Return whether the charge belongs to one of the selected subscriptions."""
        return read_path(charge, _SUBSCRIPTION_ID) in self.subscription_ids


def _plausible_year_month(raw: str | None) -> tuple[Year, Month] | None:
    """Parse an ISO timestamp into ``(year, month)``, or ``None`` when it is unusable.

    The platform serializes an absent date as the ``0001-01-01T00:00:00.000Z`` sentinel, which
    parses cleanly but means nothing, so any year outside the storable range counts as unusable.
    """
    if raw is None:
        return None
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if not is_storable_year(moment.year):
        return None
    return moment.year, Month(moment.month)


def _year_month(charge: StatementCharge) -> tuple[Year | None, Month | None]:
    """Derive ``(year, month)`` from the charge's billing period end.

    Falls back to the owning statement's cancelled/issued date when the charge has no usable
    period, and returns ``(None, None)`` when no candidate is present, parseable and plausible.
    """
    for path in _DATE_PATHS:
        found = _plausible_year_month(read_path(charge, path))
        if found is not None:
            return found
    return _UNKNOWN_YEAR_MONTH


class AccumulationKey(NamedTuple):
    """Identifies one (agreement, subscription, year, month) accumulation bucket."""

    agreement_id: str
    subscription_id: str
    year: Year | None
    month: Month | None

    @classmethod
    def from_charge(cls, charge: StatementCharge) -> Self:
        """Derive the bucket key from a charge and its owning statement.

        The subscription id falls back to ``agreement_additional_<agreement_id>`` for charges
        without a subscription, and the agreement id falls back to ``-`` when absent.
        """
        agreement_id = read_path(charge, _AGREEMENT_ID) or _UNKNOWN_ID
        subscription_id = (
            read_path(charge, _SUBSCRIPTION_ID) or f"{ADDITIONAL_AGREEMENT_PREFIX}{agreement_id}"
        )
        year, month = _year_month(charge)
        return cls(agreement_id, subscription_id, year, month)


@dataclass
class ChargeAccumulation:
    """Accumulated price totals for one (agreement, subscription, year, month) bucket."""

    agreement_id: str
    subscription_id: str
    year: Year | None
    month: Month | None
    ppx1: Decimal = Decimal(0)
    spx1: Decimal = Decimal(0)

    @classmethod
    def from_charge(cls, charge: StatementCharge) -> Self:
        """Build a single-charge accumulation: its bucket key plus this charge's prices."""
        key = AccumulationKey.from_charge(charge)
        return cls(
            key.agreement_id,
            key.subscription_id,
            key.year,
            key.month,
            ppx1=Decimal(charge.price.ppx1 or 0),  # type: ignore[union-attr]
            spx1=Decimal(charge.price.spx1 or 0),  # type: ignore[union-attr]
        )

    @property
    def key(self) -> AccumulationKey:
        """Return the accumulation key identifying this bucket."""
        return AccumulationKey(self.agreement_id, self.subscription_id, self.year, self.month)

    def storable_period(self) -> tuple[Year, Month] | None:
        """Return this bucket's ``(year, month)``, or None when the tables cannot store it."""
        if self.year is None or self.month is None:
            return None
        return (self.year, self.month) if is_storable_year(self.year) else None


@dataclass
class ChargeTotals:
    """Aggregate charge totals for a run, grouped per accumulation key."""

    charge_count: int = 0
    accumulations: dict[AccumulationKey, ChargeAccumulation] = field(default_factory=dict)

    def accumulate(self, charge: ChargeAccumulation) -> None:
        """Fold a single-charge accumulation into its bucket, summing the prices."""
        self.charge_count += 1
        bucket = self._bucket(charge.key)
        bucket.ppx1 += charge.ppx1
        bucket.spx1 += charge.spx1

    def _bucket(self, key: AccumulationKey) -> ChargeAccumulation:
        return self.accumulations.setdefault(
            key,
            ChargeAccumulation(key.agreement_id, key.subscription_id, key.year, key.month),
        )
