from types import SimpleNamespace
from typing import Any, cast

import pytest
from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.exceptions import UpstreamSubscriptionError
from mpt_usage_reporting_extension.selectors import ProductSelector, SellerSelector
from mpt_usage_reporting_extension.services.scope_resolver import ScopeResolver


class _StubSubscriptions:
    """commerce.subscriptions stub: records the RQL query and streams agreement ids."""

    def __init__(self):
        self.agreements: list[str | None] = []
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
        for agreement_id in self.agreements:
            agreement = None if agreement_id is None else SimpleNamespace(id=agreement_id)
            yield SimpleNamespace(agreement=agreement)


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


@pytest.fixture
def subscriptions():
    return _StubSubscriptions()


@pytest.fixture
def subscription_repo(mocker):
    repo = mocker.AsyncMock()
    repo.agreements_by_subscription = mocker.Mock(
        side_effect=lambda subscription_id: _aiter(["AGR-1"])
    )
    return repo


@pytest.fixture
def resolver(subscriptions, subscription_repo):
    return ScopeResolver(cast(Any, subscriptions), subscription_repo)


def test_query_for_product_scope(resolver):
    expected = RQLQuery().n("product.id").eq("PRD-1")

    result = resolver.query_for(ProductSelector("PRD-1"))

    assert str(result) == str(expected)


def test_query_for_seller_scope(resolver):
    expected = RQLQuery().n("seller.id").eq("SEL-1")

    result = resolver.query_for(SellerSelector("SEL-1"))

    assert str(result) == str(expected)


async def test_agreement_ids_dedupes_and_skips_missing(resolver, subscriptions):
    subscriptions.agreements = ["AGR-1", None, "AGR-1", "AGR-2"]

    result = [agreement_id async for agreement_id in resolver.agreement_ids(RQLQuery())]

    assert result == ["AGR-1", "AGR-2"]


async def test_agreement_ids_wraps_upstream_error(resolver, subscriptions, caplog):
    subscriptions.error = MPTError("boom")

    with pytest.raises(UpstreamSubscriptionError):
        [agreement_id async for agreement_id in resolver.agreement_ids(RQLQuery())]  # act

    assert "Upstream error resolving agreement ids from subscriptions" in caplog.text


async def test_subscription_agreements_streams_stored_agreements(resolver, subscription_repo):
    result = [agreement_id async for agreement_id in resolver.subscription_agreements("SUB-1")]

    assert result == ["AGR-1"]
    subscription_repo.agreements_by_subscription.assert_called_once_with("SUB-1")
