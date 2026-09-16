import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, Self, override

from mpt_api_client.exceptions import MPTError
from mpt_api_client.rql import RQLQuery

from mpt_usage_reporting_extension.exceptions import UpstreamExchangeRateError
from mpt_usage_reporting_extension.utils import sanitize_log_value

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CurrencyPair:
    """A source-to-destination currency pair, identified by ISO codes.

    Direction is part of the identity. The platform quotes each direction separately and the
    two are not reciprocals, so ``VND->USD`` is a different pair from ``USD->VND`` and one
    can never be derived from the other.
    """

    source: str
    destination: str

    @override
    def __str__(self) -> str:
        """Render the pair as ``SOURCE->DESTINATION`` for log lines."""
        return f"{self.source}->{self.destination}"


class ExchangePairsService(Protocol):
    """The slice of the platform's exchange-pairs service this extension needs.

    Declared locally rather than imported so the extension depends on the shape it uses,
    not on the client's untyped resource models.
    """

    def filter(self, rql: RQLQuery) -> Self:
        """Return a copy of the service narrowed by the query."""
        ...

    async def fetch_page(self, limit: int = 100, offset: int = 0) -> Any:
        """Fetch one page of matching currency pairs."""
        ...


class CurrencyConverter(Protocol):
    """Convert an amount between two currencies."""

    async def convert(self, amount: Decimal, pair: CurrencyPair) -> Decimal:
        """Return the amount expressed in the pair's destination currency."""
        ...


class MPTCurrencyConverter(CurrencyConverter):
    """Convert amounts using the platform's exchange rates, caching each rate for the run.

    Rates are cached per pair for the lifetime of one command run, so every subscription in a
    run converts at an identical rate and the run is internally consistent. The cache has no
    expiry: a later run fetches afresh.
    """

    def __init__(self, pairs: ExchangePairsService) -> None:
        self._pairs = pairs
        self._rates: dict[CurrencyPair, Decimal] = {}

    @override
    async def convert(self, amount: Decimal, pair: CurrencyPair) -> Decimal:
        """Return the amount expressed in the pair's destination currency.

        Converting within one currency is a no-op and never calls the platform. The result is
        left unrounded: rounding money mid-calculation loses precision that the caller may need.
        """
        if pair.source == pair.destination:
            return amount
        return amount * await self.rate(pair)

    async def rate(self, pair: CurrencyPair) -> Decimal:
        """Return the platform's quoted rate for this direction, cached for the run.

        Only the pair for the direction being converted is consulted. The platform quotes each
        direction separately and the two carry a spread, so the opposite pair's rate is not the
        reciprocal of this one: deriving one from the other would invent a rate the platform
        never quoted. A direction the platform does not define is an error, not a calculation.
        """
        cached = self._rates.get(pair)
        if cached is not None:
            return cached
        resolved = await self._latest_rate(pair)
        if resolved is None:
            raise UpstreamExchangeRateError(f"No exchange rate available for {pair}")
        logger.info("Resolved exchange rate %s = %s", pair, resolved)
        self._rates[pair] = resolved
        return resolved

    async def _latest_rate(self, pair: CurrencyPair) -> Decimal | None:
        """Return the pair's latest rate, or None when the platform defines no such pair.

        The filter uses the API's own property paths. ``source.code`` and any other spelling
        are rejected with a 400 Invalid property path.
        """
        query = RQLQuery(
            sourceCurrency__code=pair.source,
            destinationCurrency__code=pair.destination,
        )
        try:
            page = await self._pairs.filter(query).fetch_page(limit=1)
        except MPTError as exc:
            raise UpstreamExchangeRateError(f"Failed to fetch exchange pair {pair}") from exc
        found = next(iter(page), None)
        if found is None:
            return None
        return self._read_rate(found, pair)

    @staticmethod
    def _read_rate(found: Any, pair: CurrencyPair) -> Decimal:  # noqa: WPS602
        """Read ``latestRate.value`` off a pair resource as an exact Decimal.

        The platform serializes the amount as a JSON number, sometimes in exponent form
        (``3.861e-05``), so it is stringified before parsing: building a Decimal from a float
        would reintroduce binary rounding into a monetary conversion.
        """
        latest = getattr(found, "latest_rate", None)
        raw = getattr(latest, "value", None)
        if raw is None:
            raise UpstreamExchangeRateError(f"Exchange pair {pair} carries no latest rate")
        try:
            rate = Decimal(str(raw))
        except ArithmeticError as exc:
            raise UpstreamExchangeRateError(
                f"Exchange pair {pair} carries an unparseable rate {sanitize_log_value(str(raw))}"
            ) from exc
        if rate <= 0:
            raise UpstreamExchangeRateError(f"Exchange pair {pair} carries a non-positive rate")
        return rate
