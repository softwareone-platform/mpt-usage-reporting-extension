from types import SimpleNamespace

import pytest
from mpt_api_client import RQLQuery

from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    ProductSelector,
    SellerSelector,
    SubscriptionSelector,
)
from mpt_usage_reporting_extension.services.bucket_delete import BucketDeleter, DeleteOutcome


class _StubSubscriptions:
    """commerce.subscriptions stub: records the RQL query and streams agreement ids."""

    def __init__(self):
        self.agreements: list[str | None] = []
        self.query = None

    def filter(self, query):
        self.query = query
        return self

    def select(self, *fields):
        return self

    async def iterate(self):
        for agreement_id in self.agreements:
            agreement = None if agreement_id is None else SimpleNamespace(id=agreement_id)
            yield SimpleNamespace(agreement=agreement)


@pytest.fixture
def subscriptions():
    return _StubSubscriptions()


@pytest.fixture
def subscription_repo(mocker):
    repo = mocker.AsyncMock()
    repo.delete.return_value = 1
    return repo


@pytest.fixture
def agreement_repo(mocker):
    repo = mocker.AsyncMock()
    repo.delete.return_value = 1
    return repo


@pytest.fixture
def deleter(subscription_repo, agreement_repo, subscriptions):
    return BucketDeleter(subscription_repo, agreement_repo, subscriptions)


async def test_delete_subscription_leaves_agreement(deleter, subscription_repo, agreement_repo):
    result = await deleter.delete(SubscriptionSelector("SUB-1"))

    assert result == DeleteOutcome(1, 0)
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_not_called()


async def test_delete_agreement_clears_both_tables(deleter, subscription_repo, agreement_repo):
    result = await deleter.delete(AgreementSelector("AGR-1"))

    assert result == DeleteOutcome(1, 1)
    subscription_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")
    agreement_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")


async def test_delete_product_dedupes_agreements(deleter, subscription_repo, subscriptions):
    subscriptions.agreements = ["AGR-1", "AGR-1", "AGR-2"]

    result = await deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(2, 2)
    assert subscription_repo.delete.await_count == 2
    subscription_repo.delete.assert_any_call(agreement_id="AGR-1")
    subscription_repo.delete.assert_any_call(agreement_id="AGR-2")


async def test_delete_product_uses_product_query(deleter, subscriptions):
    subscriptions.agreements = ["AGR-1"]
    expected = RQLQuery().n("product.id").eq("PRD-1")

    await deleter.delete(ProductSelector("PRD-1"))  # act

    assert str(subscriptions.query) == str(expected)


async def test_delete_seller_uses_seller_query(deleter, subscriptions):
    subscriptions.agreements = ["AGR-1"]
    expected = RQLQuery().n("seller.id").eq("SEL-1")

    await deleter.delete(SellerSelector("SEL-1"))  # act

    assert str(subscriptions.query) == str(expected)


async def test_delete_skips_missing_agreement(deleter, subscription_repo, subscriptions):
    subscriptions.agreements = [None, "AGR-1"]

    result = await deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(1, 1)
    subscription_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")


async def test_delete_none_clears_everything(deleter, subscription_repo, agreement_repo):
    result = await deleter.delete(None)

    assert result == DeleteOutcome(1, 1)
    subscription_repo.delete.assert_awaited_once_with()
    agreement_repo.delete.assert_awaited_once_with()


async def test_delete_reports_the_summary(deleter, capsys):
    await deleter.delete(AgreementSelector("AGR-1"))  # act

    assert "Deleted 1 subscription and 1 agreement bucket(s)" in capsys.readouterr().out
