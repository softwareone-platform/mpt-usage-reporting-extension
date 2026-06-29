from types import SimpleNamespace
from typing import Any, cast

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


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


@pytest.fixture
def subscriptions():
    return _StubSubscriptions()


@pytest.fixture
def subscription_repo(mocker):
    repo = mocker.AsyncMock()
    repo.delete.return_value = 1
    # the subscription's stored agreement(s), read before deletion to narrow statement selection
    repo.stored_agreements = ["AGR-1"]
    repo.agreements_by_subscription = mocker.Mock(
        side_effect=lambda subscription_id: _aiter(repo.stored_agreements)
    )
    repo.subscriptions_by_agreement = mocker.Mock(side_effect=lambda agreement_id=None: _aiter([]))
    return repo


@pytest.fixture
def agreement_repo(mocker):
    repo = mocker.AsyncMock()
    repo.delete.return_value = 1
    return repo


@pytest.fixture
def deleter(subscription_repo, agreement_repo, subscriptions):
    return BucketDeleter(subscription_repo, agreement_repo, cast(Any, subscriptions))


async def test_delete_subscription_leaves_agreement(deleter, subscription_repo, agreement_repo):
    result = await deleter.delete(SubscriptionSelector("SUB-1"))

    assert result == DeleteOutcome(subscriptions=["SUB-1"])
    subscription_repo.agreements_by_subscription.assert_called_once_with("SUB-1")
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_not_called()


async def test_delete_subscription_reads_agreements_before_delete(deleter, subscription_repo):
    subscription_repo.stored_agreements = ["AGR-9"]

    async def _delete(**_kwargs):  # noqa: RUF029  # async to match the AsyncMock side_effect
        subscription_repo.stored_agreements = []
        return 1

    subscription_repo.delete.side_effect = _delete

    await deleter.delete(SubscriptionSelector("SUB-1"))

    assert deleter.statement_agreements == frozenset(("AGR-9",))


async def test_delete_agreement_clears_both_tables(deleter, subscription_repo, agreement_repo):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    result = await deleter.delete(AgreementSelector("AGR-1"))

    assert result == DeleteOutcome(subscriptions=["SUB-1"], agreements=["AGR-1"])
    subscription_repo.subscriptions_by_agreement.assert_called_once_with("AGR-1")
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")


async def test_delete_product_dedupes_agreements(deleter, subscription_repo, subscriptions):
    subscriptions.agreements = ["AGR-1", "AGR-1", "AGR-2"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter(
        [f"SUB-{agreement_id}"] if agreement_id else []
    )

    result = await deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-AGR-1", "SUB-AGR-2"],
        agreements=["AGR-1", "AGR-2"],
    )
    assert subscription_repo.delete.await_count == 2
    subscription_repo.delete.assert_any_call(subscription_id="SUB-AGR-1")
    subscription_repo.delete.assert_any_call(subscription_id="SUB-AGR-2")


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
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])

    result = await deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(subscriptions=["SUB-1"], agreements=["AGR-1"])
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")


async def test_delete_product_zero_delete_keeps_scope_empty(
    deleter, subscription_repo, agreement_repo, subscriptions
):
    subscriptions.agreements = ["AGR-1"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([])
    agreement_repo.delete.return_value = 0

    result = await deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome()
    subscription_repo.subscriptions_by_agreement.assert_called_once_with("AGR-1")
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")


async def test_delete_none_clears_everything(deleter, subscription_repo, agreement_repo):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    result = await deleter.delete(None)

    assert result == DeleteOutcome(subscriptions=["SUB-1"])
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_awaited_once_with()


async def test_delete_reports_the_summary(deleter, subscription_repo, caplog):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])

    caplog.set_level("INFO")
    await deleter.delete(AgreementSelector("AGR-1"))  # act

    assert "Deleted subscription: SUB-1" in caplog.text
    assert "Deleted agreement: AGR-1" in caplog.text
    assert "Deleted 2 bucket(s) (1 subscription, 1 agreement)" in caplog.text


async def test_delete_agreement_without_subscriptions_keeps_agreement_reset_target(
    deleter, subscription_repo, agreement_repo
):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([])
    agreement_repo.delete.return_value = 1

    result = await deleter.delete(AgreementSelector("AGR-9"))

    assert result == DeleteOutcome(agreements=["AGR-9"])


async def test_delete_subscription_dry_run_skips_delete_calls(
    subscription_repo, agreement_repo, subscriptions
):
    subscription_repo.stored_agreements = ["AGR-1"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    deleter = BucketDeleter(
        subscription_repo,
        agreement_repo,
        cast(Any, subscriptions),
        dry_run=True,
    )

    result = await deleter.delete(SubscriptionSelector("SUB-1"))

    assert result == DeleteOutcome(subscriptions=["SUB-1"])
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_not_called()


async def test_delete_agreement_dry_run_uses_scope_without_writes(
    subscription_repo, agreement_repo, subscriptions
):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    deleter = BucketDeleter(
        subscription_repo,
        agreement_repo,
        cast(Any, subscriptions),
        dry_run=True,
    )

    result = await deleter.delete(AgreementSelector("AGR-1"))

    assert result == DeleteOutcome(subscriptions=["SUB-1"])
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_not_called()
    assert deleter.statement_agreements == frozenset(("AGR-1",))
