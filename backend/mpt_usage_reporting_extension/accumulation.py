import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple, Self

from mpt_api_client.resources.billing.statement_charges import StatementCharge

from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.exceptions import ChargePriceError
from mpt_usage_reporting_extension.types import Month, Year, is_storable_year

_AGREEMENT_ID = "agreement.id"
_SUBSCRIPTION_ID = "subscription.id"
_CHARGE_PERIOD_END = "period.end"
_STATEMENT_CANCELLED_AT = "statement.audit.cancelled.at"
_STATEMENT_ISSUED_AT = "statement.audit.issued.at"
_PURCHASE_CURRENCY = "price.currency.purchase"
_SALE_CURRENCY = "price.currency.sale"
# The API client snake-cases ``BSPx1`` as ``bs_px1``; the tests build real charges, so a mapping
# change in the client would surface there.
_BSPX1 = "price.bs_px1"
_SPX1 = "price.spx1"
_PPX1 = "price.ppx1"
_CHARGE_ID = "id"
_STATEMENT_ID = "statement.id"
_UNKNOWN_ID = "-"
_UNKNOWN_YEAR_MONTH: tuple[None, None] = (None, None)
_DATE_PATHS = (_CHARGE_PERIOD_END, _STATEMENT_CANCELLED_AT, _STATEMENT_ISSUED_AT)


def read_path(resource: object, path: str) -> str | None:
    """Walk a dot-notated attribute path, returning None when any segment is missing."""
    current: Any = resource
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
    """Accumulated price totals for one (agreement, subscription, year, month) bucket.

    ``spx1`` sums each charge's ``BSPx1``: the sale price in the purchase currency, which is the
    authorization's currency and so the one the subscription's own price is denominated in. The
    charge's ``SPx1`` is the same amount converted to the agreement's billing currency and must
    not be summed into a subscription price. ``currencies`` collects the purchase currency of
    every charge folded in - normally one; a charge without one contributes nothing.
    """

    agreement_id: str
    subscription_id: str
    year: Year | None
    month: Month | None
    ppx1: Decimal = Decimal(0)
    spx1: Decimal = Decimal(0)
    currencies: set[str] = field(default_factory=set)

    @classmethod
    def from_charge(cls, charge: StatementCharge) -> Self:
        """Build a single-charge accumulation: its bucket key, prices, and purchase currency.

        Raises:
            ChargePriceError: the charge carries no ``PPx1``, or no ``BSPx1`` and no ``SPx1`` in
                its purchase currency.
        """
        key = AccumulationKey.from_charge(charge)
        bspx1 = cls._sale_price_in_price_currency(charge)
        purchase_currency = read_path(charge, _PURCHASE_CURRENCY)
        return cls(
            key.agreement_id,
            key.subscription_id,
            key.year,
            key.month,
            ppx1=cls._required_price(charge, _PPX1, "PPx1"),
            # the subscription's SP is the charge's BSPx1; the charge's SPx1 is in billing currency
            spx1=bspx1,
            currencies=set() if purchase_currency is None else {purchase_currency},
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

    @staticmethod
    def _sale_price_in_price_currency(charge: StatementCharge) -> Decimal:  # noqa: WPS602
        """Return the charge's ``BSPx1``, the sale price in the subscription's price currency.

        Charges issued before the platform added ``BSPx1`` lack it. When such a charge was sold in
        its purchase currency, its ``SPx1`` is in that same currency and is the same amount, so it
        is used instead. Otherwise ``SPx1`` is in another currency and a missing amount is not a
        zero one, so accumulation fails: a charge is never counted as 0 or in the wrong currency.

        Raises:
            ChargePriceError: the charge carries no ``BSPx1``, and no ``SPx1`` in its purchase
                currency either.
        """
        purchase_currency = read_path(charge, _PURCHASE_CURRENCY)
        sold_in_purchase_currency = (
            purchase_currency is not None and purchase_currency == read_path(charge, _SALE_CURRENCY)
        )
        if read_path(charge, _BSPX1) is None and sold_in_purchase_currency:
            return ChargeAccumulation._required_price(charge, _SPX1, "BSPx1 or SPx1")
        return ChargeAccumulation._required_price(charge, _BSPX1, "BSPx1")

    @staticmethod
    def _required_price(charge: StatementCharge, path: str, name: str) -> Decimal:  # noqa: WPS602
        """Return a price the charge must carry, failing with the charge and statement ids.

        Raises:
            ChargePriceError: the charge does not carry the price.
        """
        amount = read_path(charge, path)
        if amount is not None:
            return Decimal(amount)
        charge_id = read_path(charge, _CHARGE_ID) or "-"
        statement_id = read_path(charge, _STATEMENT_ID) or "-"
        raise ChargePriceError(
            f"Charge {charge_id} of statement {statement_id} has no {name}, so it cannot be summed"
        )


@dataclass
class ChargeTotals:
    """Aggregate charge totals for a run, grouped per accumulation key.

    ``failed_agreements`` maps each agreement left out, because one of its charges could not be
    summed, to the reason; none of its buckets is kept.
    """

    charge_count: int = 0
    accumulations: dict[AccumulationKey, ChargeAccumulation] = field(default_factory=dict)
    failed_agreements: dict[str, str] = field(default_factory=dict)

    def accumulate(self, charge: ChargeAccumulation) -> None:
        """Fold a single-charge accumulation into its bucket, summing prices and currencies."""
        self.charge_count += 1
        self._fold(charge)

    def merge(self, other: "ChargeTotals") -> None:
        """Fold another run's totals - one statement's, typically - into these."""
        self.charge_count += other.charge_count
        for bucket in other.accumulations.values():
            self._fold(bucket)

    def fail_agreement(self, agreement_id: str, reason: str) -> None:
        """Leave the agreement out: drop every bucket of it and record why."""
        self.failed_agreements[agreement_id] = reason
        self.accumulations = {
            key: bucket
            for key, bucket in self.accumulations.items()
            if key.agreement_id != agreement_id
        }

    def _fold(self, accumulation: ChargeAccumulation) -> None:
        bucket = self._bucket(accumulation.key)
        bucket.ppx1 += accumulation.ppx1
        bucket.spx1 += accumulation.spx1
        bucket.currencies |= accumulation.currencies

    def _bucket(self, key: AccumulationKey) -> ChargeAccumulation:
        return self.accumulations.setdefault(
            key,
            ChargeAccumulation(key.agreement_id, key.subscription_id, key.year, key.month),
        )
