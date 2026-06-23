import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import assert_never

import typer
from mpt_api_client import RQLQuery
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService

from mpt_usage_reporting_extension.persistence.protocols import (
    AgreementAccumulationRepository,
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.selectors import (
    AgreementSelector,
    ProductSelector,
    Selector,
    SellerSelector,
    SubscriptionSelector,
)

logger = logging.getLogger(__name__)

_PRODUCT_ID = "product.id"
_SELLER_ID = "seller.id"


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    """How many buckets each accumulation table shed for a delete scope and month range."""

    subscription_deleted: int
    agreement_deleted: int


class DeleteReport:
    """Render the outcome of a bucket delete as a one-line summary."""

    def __init__(self, outcome: DeleteOutcome) -> None:
        self._outcome = outcome

    def render(self) -> None:
        """Print the summary line for the delete."""
        typer.echo(self._summary())

    def _summary(self) -> str:
        subscription = self._outcome.subscription_deleted
        agreement = self._outcome.agreement_deleted
        return f"Deleted {subscription} subscription and {agreement} agreement bucket(s)"


class BucketDeleter:  # noqa: WPS214
    """Delete all stored accumulation buckets for a scope.

    Product and seller scopes are resolved to their agreement ids against the commerce API (an
    agreement belongs to one product and one seller, so deleting by ``agreement_id`` covers every
    subscription of each matched agreement). The subscription scope deletes only the subscription
    bucket, since the shared agreement bucket aggregates its sibling subscriptions. A ``None`` scope
    deletes every bucket in both tables.
    """

    def __init__(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        agreement_repo: AgreementAccumulationRepository,
        subscriptions: AsyncSubscriptionsService,
    ) -> None:
        self._subscription_repo = subscription_repo
        self._agreement_repo = agreement_repo
        self._subscriptions = subscriptions

    async def delete(self, scope: Selector | None) -> DeleteOutcome:
        """Delete the scope's buckets from both tables, then report."""
        outcome = await self._delete_scope(scope)
        logger.info(
            "Deleted %d subscription and %d agreement bucket(s)",
            outcome.subscription_deleted,
            outcome.agreement_deleted,
        )
        DeleteReport(outcome).render()
        return outcome

    async def _delete_scope(self, scope: Selector | None) -> DeleteOutcome:
        if scope is None:
            return await self._delete_all()
        match scope:
            case SubscriptionSelector(subscription_id):
                deleted = await self._subscription_repo.delete(subscription_id=subscription_id)
                return DeleteOutcome(deleted, 0)
            case AgreementSelector(agreement_id):
                return await self._delete_agreement(agreement_id)
            case ProductSelector(product_id):
                return await self._delete_query(RQLQuery().n(_PRODUCT_ID).eq(product_id))
            case SellerSelector(seller_id):
                return await self._delete_query(RQLQuery().n(_SELLER_ID).eq(seller_id))
        assert_never(scope)  # pragma: no cover

    async def _delete_all(self) -> DeleteOutcome:
        subscription_deleted = await self._subscription_repo.delete()
        agreement_deleted = await self._agreement_repo.delete()
        return DeleteOutcome(subscription_deleted, agreement_deleted)

    async def _delete_agreement(self, agreement_id: str) -> DeleteOutcome:
        subscription_deleted = await self._subscription_repo.delete(agreement_id=agreement_id)
        agreement_deleted = await self._agreement_repo.delete(agreement_id=agreement_id)
        return DeleteOutcome(subscription_deleted, agreement_deleted)

    async def _delete_query(self, query: RQLQuery) -> DeleteOutcome:
        subscription_deleted = 0
        agreement_deleted = 0
        async for agreement_id in self._agreement_ids(query):
            outcome = await self._delete_agreement(agreement_id)
            subscription_deleted += outcome.subscription_deleted
            agreement_deleted += outcome.agreement_deleted
        return DeleteOutcome(subscription_deleted, agreement_deleted)

    async def _agreement_ids(self, query: RQLQuery) -> AsyncIterator[str]:
        """Stream the distinct agreement ids of the subscriptions matching the query."""
        seen: set[str] = set()
        async for sub in self._subscriptions.filter(query).select("agreement.id").iterate():
            agreement = getattr(sub, "agreement", None)
            agreement_id = getattr(agreement, "id", None)
            if agreement_id and agreement_id not in seen:
                seen.add(agreement_id)
                yield agreement_id
