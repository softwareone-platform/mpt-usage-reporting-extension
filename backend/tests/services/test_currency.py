from decimal import Decimal

import pytest
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.exceptions import UpstreamExchangeRateError
from mpt_usage_reporting_extension.services.currency import (
    CurrencyPair,
    MPTCurrencyConverter,
)

VND_TO_USD = CurrencyPair(source="VND", destination="USD")
USD_TO_USD = CurrencyPair(source="USD", destination="USD")

# The live VND:USD rate, kept as text so it stays the wire representation the API sends
# rather than becoming a float literal in the test source.
LIVE_VND_USD_RATE = float("3.861e-05")


class FakePairsService:
    """Stand-in for the platform's exchange-pairs service, recording every query it is given."""

    def __init__(self, pages):
        self.pages = pages
        self.queries = []
        self.fetch_count = 0

    def filter(self, rql):
        self.queries.append(str(rql))
        return self

    async def fetch_page(self, limit=100, offset=0):
        self.fetch_count += 1
        return self.pages.pop(0)


class FakePair:
    """A pair resource exposing ``latest_rate.value`` the way the client model does."""

    def __init__(self, rate):
        self.latest_rate = FakeRate(rate)


class FakeRate:
    def __init__(self, rate):
        self.value = rate  # noqa: WPS110  # the API's own field name for the rate amount


@pytest.fixture
def pairs():
    return FakePairsService([[FakePair("0.0000386")]])


@pytest.fixture
def converter(pairs):
    return MPTCurrencyConverter(pairs)


async def test_convert_applies_the_rate(converter):
    result = await converter.convert(Decimal(1000000), VND_TO_USD)

    assert result == Decimal("38.6")


async def test_convert_keeps_full_precision(pairs):
    pairs.pages = [[FakePair("0.00003857")]]
    converter = MPTCurrencyConverter(pairs)

    result = await converter.convert(Decimal("306304679.24"), VND_TO_USD)

    assert result == Decimal("306304679.24") * Decimal("0.00003857")


async def test_convert_within_one_currency_is_a_no_op(converter):
    result = await converter.convert(Decimal("123.45"), USD_TO_USD)

    assert result == Decimal("123.45")


async def test_convert_within_one_currency_never_calls_the_platform(converter, pairs):
    await converter.convert(Decimal("123.45"), USD_TO_USD)

    assert pairs.fetch_count == 0


async def test_rate_queries_both_currency_codes(converter, pairs):
    """The API accepts only these property paths; ``source.code`` returns a 400."""
    await converter.rate(VND_TO_USD)

    assert "eq(sourceCurrency.code,'VND')" in pairs.queries[0]
    assert "eq(destinationCurrency.code,'USD')" in pairs.queries[0]


async def test_rate_is_cached_for_the_run(converter, pairs):
    await converter.rate(VND_TO_USD)
    await converter.rate(VND_TO_USD)

    assert pairs.fetch_count == 1


async def test_rate_parses_a_numeric_rate(pairs):
    pairs.pages = [[FakePair(0.5)]]
    converter = MPTCurrencyConverter(pairs)

    result = await converter.rate(VND_TO_USD)

    assert result == Decimal("0.5")


async def test_rate_parses_the_exponent_form_the_api_returns(pairs):
    """VND:USD really comes back as 3.861e-05, and must not lose precision via float."""
    pairs.pages = [[FakePair(LIVE_VND_USD_RATE)]]
    converter = MPTCurrencyConverter(pairs)

    result = await converter.rate(VND_TO_USD)

    assert result == Decimal("3.861e-05")


async def test_convert_reproduces_the_ticket_conversion(pairs):
    """The GIMO statement from MPT-25312: ~306M VND is a bill of ~11.8K USD, not ~306M."""
    pairs.pages = [[FakePair(LIVE_VND_USD_RATE)]]
    converter = MPTCurrencyConverter(pairs)

    result = await converter.convert(Decimal("306304679.24"), VND_TO_USD)

    assert result.quantize(Decimal("0.01")) == Decimal("11826.42")


async def test_rate_raises_when_the_direction_is_undefined(pairs):
    """A direction the platform does not quote is an error, never a derived number."""
    pairs.pages = [[]]
    converter = MPTCurrencyConverter(pairs)

    with pytest.raises(UpstreamExchangeRateError, match="No exchange rate available"):
        await converter.rate(VND_TO_USD)


async def test_rate_never_falls_back_to_the_opposite_direction(pairs):
    """Each direction is quoted separately and carries a spread, so one is never inverted."""
    pairs.pages = [[], [FakePair("25903.03970661")]]
    converter = MPTCurrencyConverter(pairs)

    with pytest.raises(UpstreamExchangeRateError):
        await converter.rate(VND_TO_USD)

    assert pairs.fetch_count == 1


async def test_each_direction_is_cached_separately(pairs):
    """USD->VND is a distinct pair from VND->USD, so it gets its own lookup and rate."""
    pairs.pages = [[FakePair(LIVE_VND_USD_RATE)], [FakePair("25903.03970661")]]
    converter = MPTCurrencyConverter(pairs)

    result = (
        await converter.rate(VND_TO_USD),
        await converter.rate(CurrencyPair(source="USD", destination="VND")),
    )

    assert result == (Decimal("3.861e-05"), Decimal("25903.03970661"))


async def test_rate_raises_when_the_pair_carries_no_rate(pairs):
    pairs.pages = [[FakePair(None)], []]
    converter = MPTCurrencyConverter(pairs)

    with pytest.raises(UpstreamExchangeRateError, match="carries no latest rate"):
        await converter.rate(VND_TO_USD)


async def test_rate_raises_on_a_non_positive_rate(pairs):
    pairs.pages = [[FakePair("0")]]
    converter = MPTCurrencyConverter(pairs)

    with pytest.raises(UpstreamExchangeRateError, match="non-positive rate"):
        await converter.rate(VND_TO_USD)


async def test_rate_raises_on_an_unparseable_rate(pairs):
    pairs.pages = [[FakePair("not-a-number")]]
    converter = MPTCurrencyConverter(pairs)

    with pytest.raises(UpstreamExchangeRateError, match="unparseable rate"):
        await converter.rate(VND_TO_USD)


async def test_rate_wraps_an_upstream_failure(mocker):
    pairs = mocker.Mock()
    pairs.filter.return_value = pairs
    pairs.fetch_page = mocker.AsyncMock(side_effect=MPTError("boom"))
    converter = MPTCurrencyConverter(pairs)

    with pytest.raises(UpstreamExchangeRateError, match="Failed to fetch exchange pair"):
        await converter.rate(VND_TO_USD)


def test_pair_direction_is_part_of_its_identity():
    result = CurrencyPair(source="USD", destination="VND")

    assert result != VND_TO_USD


def test_pair_renders_for_logs():
    result = str(VND_TO_USD)

    assert result == "VND->USD"
