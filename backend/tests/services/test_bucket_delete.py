from types import SimpleNamespace
from typing import Any, cast

import pytest
from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.exceptions import UpstreamSubscriptionError
from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    ProductSelector,
    SellerSelector,
    SubscriptionSelector,
)
from mpt_usage_reporting_extension.services.bucket_delete import (
    BucketDeleter,
    DeleteOutcome,
    ScopeBucketDeleter,
)
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
def resolver(subscriptions, subscription_repo):
    return ScopeResolver(cast(Any, subscriptions), subscription_repo)


@pytest.fixture
def deleter(subscription_repo, agreement_repo, resolver):
    return BucketDeleter(subscription_repo, agreement_repo, resolver)


@pytest.fixture
def scope_deleter(deleter, resolver):
    return ScopeBucketDeleter(deleter, resolver)


async def test_delete_subscription_leaves_agreement(
    scope_deleter, subscription_repo, agreement_repo
):
    result = await scope_deleter.delete(SubscriptionSelector("SUB-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-1"], statement_agreements=frozenset(("AGR-1",))
    )
    subscription_repo.agreements_by_subscription.assert_called_once_with("SUB-1")
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_not_called()


async def test_delete_subscription_reads_agreements_before_delete(scope_deleter, subscription_repo):
    subscription_repo.stored_agreements = ["AGR-9"]

    async def _delete(**_kwargs):  # noqa: RUF029  # async to match the AsyncMock side_effect
        subscription_repo.stored_agreements = []
        return 1

    subscription_repo.delete.side_effect = _delete

    result = await scope_deleter.delete(SubscriptionSelector("SUB-1"))

    assert result.statement_agreements == frozenset(("AGR-9",))


async def test_delete_agreement_clears_both_tables(
    scope_deleter, subscription_repo, agreement_repo
):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    result = await scope_deleter.delete(AgreementSelector("AGR-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-1"],
        agreements=["AGR-1"],
        statement_agreements=frozenset(("AGR-1",)),
    )
    subscription_repo.subscriptions_by_agreement.assert_called_once_with("AGR-1")
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")


async def test_delete_product_dedupes_agreements(scope_deleter, subscription_repo, subscriptions):
    subscriptions.agreements = ["AGR-1", "AGR-1", "AGR-2"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter(
        [f"SUB-{agreement_id}"] if agreement_id else []
    )

    result = await scope_deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-AGR-1", "SUB-AGR-2"],
        agreements=["AGR-1", "AGR-2"],
        statement_agreements=frozenset(("AGR-1", "AGR-2")),
    )
    assert subscription_repo.delete.await_count == 2
    subscription_repo.delete.assert_any_call(subscription_id="SUB-AGR-1")
    subscription_repo.delete.assert_any_call(subscription_id="SUB-AGR-2")


async def test_delete_wraps_upstream_error(scope_deleter, subscriptions, caplog):
    subscriptions.error = MPTError("boom")

    with pytest.raises(UpstreamSubscriptionError):
        await scope_deleter.delete(ProductSelector("PRD-1"))

    assert "Upstream error resolving agreement ids from subscriptions" in caplog.text


async def test_delete_product_uses_product_query(scope_deleter, subscriptions):
    subscriptions.agreements = ["AGR-1"]
    expected = RQLQuery().n("product.id").eq("PRD-1")

    await scope_deleter.delete(ProductSelector("PRD-1"))  # act

    assert str(subscriptions.query) == str(expected)


async def test_delete_seller_uses_seller_query(scope_deleter, subscriptions):
    subscriptions.agreements = ["AGR-1"]
    expected = RQLQuery().n("seller.id").eq("SEL-1")

    await scope_deleter.delete(SellerSelector("SEL-1"))  # act

    assert str(subscriptions.query) == str(expected)


async def test_delete_skips_missing_agreement(scope_deleter, subscription_repo, subscriptions):
    subscriptions.agreements = [None, "AGR-1"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])

    result = await scope_deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-1"],
        agreements=["AGR-1"],
        statement_agreements=frozenset(("AGR-1",)),
    )
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")


async def test_delete_product_zero_delete_registers_statement_agreements(
    scope_deleter, subscription_repo, agreement_repo, subscriptions
):
    subscriptions.agreements = ["AGR-1"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([])
    agreement_repo.delete.return_value = 0

    result = await scope_deleter.delete(ProductSelector("PRD-1"))

    assert result == DeleteOutcome(statement_agreements=frozenset(("AGR-1",)))
    subscription_repo.subscriptions_by_agreement.assert_called_once_with("AGR-1")
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_awaited_once_with(agreement_id="AGR-1")


async def test_delete_agreement_zero_delete_registers_statement_agreement(
    scope_deleter, subscription_repo, agreement_repo
):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([])
    agreement_repo.delete.return_value = 0

    result = await scope_deleter.delete(AgreementSelector("AGR-9"))

    assert result == DeleteOutcome(statement_agreements=frozenset(("AGR-9",)))


async def test_delete_subscription_zero_delete_returns_empty_outcome(
    scope_deleter, subscription_repo
):
    subscription_repo.stored_agreements = []
    subscription_repo.delete.return_value = 0

    result = await scope_deleter.delete(SubscriptionSelector("SUB-1"))

    assert result == DeleteOutcome()
    assert result.statement_agreements == frozenset()


async def test_delete_none_clears_everything(scope_deleter, subscription_repo, agreement_repo):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    result = await scope_deleter.delete(None)

    assert result == DeleteOutcome(subscriptions=["SUB-1"])
    subscription_repo.delete.assert_awaited_once_with(subscription_id="SUB-1")
    agreement_repo.delete.assert_awaited_once_with()


async def test_delete_reports_the_summary(scope_deleter, subscription_repo, caplog):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])

    caplog.set_level("INFO")
    await scope_deleter.delete(AgreementSelector("AGR-1"))  # act

    assert "Deleted subscription: SUB-1" in caplog.text
    assert "Deleted agreement: AGR-1" in caplog.text
    assert "Deleted 2 bucket(s) (1 subscription, 1 agreement)" in caplog.text


async def test_delete_agreement_without_subscriptions_keeps_agreement_reset_target(
    scope_deleter, subscription_repo, agreement_repo
):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([])
    agreement_repo.delete.return_value = 1

    result = await scope_deleter.delete(AgreementSelector("AGR-9"))

    assert result == DeleteOutcome(agreements=["AGR-9"], statement_agreements=frozenset(("AGR-9",)))


async def test_delete_scope_routes_each_selector_to_its_action(mocker, resolver):
    action_deleter = mocker.AsyncMock()
    scope_deleter = ScopeBucketDeleter(action_deleter, resolver)
    action_deleter.delete_subscription.return_value = DeleteOutcome()
    action_deleter.delete_agreement.return_value = DeleteOutcome()
    action_deleter.delete_agreements_by_query.return_value = DeleteOutcome()
    action_deleter.delete_all.return_value = DeleteOutcome()

    await scope_deleter.delete(SubscriptionSelector("SUB-1"))
    await scope_deleter.delete(AgreementSelector("AGR-1"))
    await scope_deleter.delete(ProductSelector("PRD-1"))
    await scope_deleter.delete(None)  # act

    action_deleter.delete_subscription.assert_awaited_once_with("SUB-1")
    action_deleter.delete_agreement.assert_awaited_once_with("AGR-1")
    action_deleter.delete_agreements_by_query.assert_awaited_once()
    action_deleter.delete_all.assert_awaited_once_with()


async def test_delete_subscription_dry_run_skips_delete_calls(
    subscription_repo, agreement_repo, resolver
):
    subscription_repo.stored_agreements = ["AGR-1"]
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    scope_deleter = ScopeBucketDeleter(
        BucketDeleter(subscription_repo, agreement_repo, resolver, dry_run=True),
        resolver,
    )

    result = await scope_deleter.delete(SubscriptionSelector("SUB-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-1"], statement_agreements=frozenset(("AGR-1",))
    )
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_not_called()


async def test_delete_none_dry_run_skips_delete_calls(subscription_repo, agreement_repo, resolver):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    scope_deleter = ScopeBucketDeleter(
        BucketDeleter(subscription_repo, agreement_repo, resolver, dry_run=True),
        resolver,
    )

    result = await scope_deleter.delete(None)

    assert result == DeleteOutcome(subscriptions=["SUB-1"])
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_not_called()


async def test_delete_agreement_dry_run_uses_scope_without_writes(
    subscription_repo, agreement_repo, resolver
):
    subscription_repo.subscriptions_by_agreement.side_effect = lambda agreement_id=None: _aiter([
        "SUB-1"
    ])
    scope_deleter = ScopeBucketDeleter(
        BucketDeleter(subscription_repo, agreement_repo, resolver, dry_run=True),
        resolver,
    )

    result = await scope_deleter.delete(AgreementSelector("AGR-1"))

    assert result == DeleteOutcome(
        subscriptions=["SUB-1"], statement_agreements=frozenset(("AGR-1",))
    )
    subscription_repo.delete.assert_not_called()
    agreement_repo.delete.assert_not_called()
