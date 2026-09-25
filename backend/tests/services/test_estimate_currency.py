import pytest
from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.commerce.agreements import Agreement

from mpt_usage_reporting_extension.exceptions import EstimateCurrencyError, UpstreamAPIError
from mpt_usage_reporting_extension.services.estimate_currency import (
    EstimateCurrencyGuard,
    charged_currencies,
    split_currency_agreement_ids,
)


class _StubAgreements:
    """commerce.agreements stub: records the RQL query and streams the agreements set on it."""

    def __init__(self):
        self.agreements: list[Agreement] = []
        self.error: Exception | None = None
        self.query = None

    def filter(self, query):
        self.query = query
        return self

    def select(self, *fields):
        return self

    async def iterate(self):
        if self.error is not None:
            raise self.error
        for agreement in self.agreements:
            yield agreement


def _agreement(agreement_id, currency=None, billing_currency=None):
    price = {}
    if currency is not None:
        price["currency"] = currency
    if billing_currency is not None:
        price["billingCurrency"] = billing_currency
    return Agreement({"id": agreement_id, "price": price})


async def _drain(agreements, product_ids):
    return [
        agreement_id async for agreement_id in split_currency_agreement_ids(agreements, product_ids)
    ]


@pytest.mark.parametrize(
    "charged",
    [
        {},
        {"SUB-1": frozenset(("USD",))},
        {"SUB-2": frozenset(("USD", "VND"))},
    ],
)
def test_verify_passes_a_single_or_unknown_currency(charged):
    guard = EstimateCurrencyGuard(charged)

    guard.verify("SUB-1")  # act


def test_verify_refuses_charges_priced_in_more_than_one_currency():
    guard = EstimateCurrencyGuard({"SUB-1": frozenset(("VND", "USD"))})

    with pytest.raises(EstimateCurrencyError, match=r"more than one currency \(USD,VND\)"):
        guard.verify("SUB-1")


async def test_split_currency_agreement_ids_yields_only_split_agreements():
    stub = _StubAgreements()
    stub.agreements = [
        _agreement("AGR-VN", "USD", "VND"),
        _agreement("AGR-US", "USD", "USD"),
        _agreement("AGR-ID", "IDR", "IDR"),
        _agreement("AGR-NO-BILLING", "USD"),
        _agreement("AGR-NO-PRICE"),
    ]

    result = await _drain(stub, ["PRD-1"])

    assert result == ["AGR-VN"]


async def test_split_currency_agreement_ids_queries_all_products_at_once():
    stub = _StubAgreements()
    expected_query = RQLQuery().n("product.id").in_(["PRD-1", "PRD-2"])

    await _drain(stub, ["PRD-1", "PRD-2"])  # act

    assert str(stub.query) == str(expected_query)


async def test_split_currency_agreement_ids_wraps_upstream_error():
    stub = _StubAgreements()
    stub.error = MPTError("boom")

    with pytest.raises(UpstreamAPIError, match="Failed to list the products' agreements"):
        await _drain(stub, ["PRD-1"])


def test_charged_currencies_unions_each_subscription(charge_accumulation_factory):
    accumulations = [
        charge_accumulation_factory("SUB-1", month=5, currencies=("USD",)),
        charge_accumulation_factory("SUB-1", month=6, currencies=("USD", "EUR")),
        charge_accumulation_factory("SUB-2", currencies=("IDR",)),
    ]

    result = charged_currencies(accumulations)

    expected = {"SUB-1": frozenset(("USD", "EUR")), "SUB-2": frozenset(("IDR",))}
    assert result == expected


def test_charged_currencies_drops_synthetic_and_currency_less_buckets(
    charge_accumulation_factory,
):
    accumulations = [
        charge_accumulation_factory("agreement_additional_AGR-1", currencies=("USD",)),
        charge_accumulation_factory("SUB-1"),
    ]

    result = charged_currencies(accumulations)

    assert not result
