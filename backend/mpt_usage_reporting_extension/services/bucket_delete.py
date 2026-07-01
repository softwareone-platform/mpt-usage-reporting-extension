import logging
from collections.abc import AsyncIterator
from typing import override

from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService

from mpt_usage_reporting_extension.exceptions import UpstreamSubscriptionError
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


class DeleteOutcome:
    """Ordered subscription ids deleted by the scope reset."""

    __slots__ = ("agreements", "subscriptions")

    def __init__(
        self,
        subscriptions: list[str] | None = None,
        agreements: list[str] | None = None,
    ) -> None:
        self.subscriptions = [] if subscriptions is None else list(subscriptions)
        self.agreements = [] if agreements is None else list(agreements)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DeleteOutcome):
            return NotImplemented
        return self.subscriptions == other.subscriptions and self.agreements == other.agreements

    @override
    def __hash__(self) -> int:
        return hash((tuple(self.subscriptions), tuple(self.agreements)))

    @override
    def __repr__(self) -> str:
        return (
            f"DeleteOutcome(subscriptions={self.subscriptions!r}, agreements={self.agreements!r})"
        )

    @classmethod
    async def from_subscriptions(
        cls,
        subscriptions: AsyncIterator[str],
        agreements: list[str] | None = None,
    ) -> "DeleteOutcome":
        """Build an outcome by consuming a stream of deleted subscription ids."""
        return cls(
            subscriptions=[subscription_id async for subscription_id in subscriptions],
            agreements=[] if agreements is None else agreements,
        )


class DeleteReport:
    """Render the outcome of a bucket delete as a one-line summary."""

    def __init__(self, outcome: DeleteOutcome) -> None:
        self._outcome = outcome

    def render(self) -> None:
        """Log each deleted bucket id, then the summary line."""
        for subscription_id in self._outcome.subscriptions:
            logger.info("Deleted subscription: %s", subscription_id)
        for agreement_id in self._outcome.agreements:
            logger.info("Deleted agreement: %s", agreement_id)
        logger.info(self._summary())

    def _summary(self) -> str:
        subscriptions = len(self._outcome.subscriptions)
        agreements = len(self._outcome.agreements)
        total = subscriptions + agreements
        return f"Deleted {total} bucket(s) ({subscriptions} subscription, {agreements} agreement)"


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
        *,
        dry_run: bool = False,
    ) -> None:
        self._subscription_repo = subscription_repo
        self._agreement_repo = agreement_repo
        self._subscriptions = subscriptions
        self._statement_agreements: frozenset[str] = frozenset()
        self._dry_run = dry_run

    @property
    def statement_agreements(self) -> frozenset[str]:
        """Return agreement ids whose statements should be re-selected."""
        return self._statement_agreements

    async def delete(self, scope: Selector | None) -> DeleteOutcome:
        """Delete the scope's buckets from both tables, then report."""
        self._statement_agreements = frozenset()
        outcome = await self._delete_scope(scope)
        logger.info(
            "Deleted %d bucket(s)",
            len(outcome.subscriptions) + len(outcome.agreements),
        )
        DeleteReport(outcome).render()
        return outcome

    async def _delete_scope(self, scope: Selector | None) -> DeleteOutcome:
        if scope is None:
            return await self._delete_all()
        match scope:
            case SubscriptionSelector(subscription_id):
                return await self._delete_subscription(subscription_id)
            case AgreementSelector(agreement_id):
                return await self._delete_agreement(agreement_id)
            case ProductSelector(product_id):
                return await self._delete_query(RQLQuery().n(_PRODUCT_ID).eq(product_id))
            case SellerSelector(seller_id):
                return await self._delete_query(RQLQuery().n(_SELLER_ID).eq(seller_id))

    async def _delete_all(self) -> DeleteOutcome:
        outcome = await DeleteOutcome.from_subscriptions(
            self._delete_subscription_ids(self._subscription_repo.subscriptions_by_agreement())
        )
        agreement_deleted = await self._agreement_repo.delete()
        if not outcome.subscriptions and agreement_deleted == 0:
            return DeleteOutcome()
        return outcome

    async def _delete_subscription(self, subscription_id: str) -> DeleteOutcome:
        agreements = [
            agreement_id
            async for agreement_id in self._subscription_repo.agreements_by_subscription(
                subscription_id
            )
        ]
        if self._dry_run:
            deleted = int(await self._has_subscription(subscription_id))
        else:
            deleted = await self._subscription_repo.delete(subscription_id=subscription_id)
        if deleted == 0:
            return DeleteOutcome()
        self._statement_agreements = frozenset(agreements)
        return DeleteOutcome(subscriptions=[subscription_id])

    async def _delete_agreement(self, agreement_id: str) -> DeleteOutcome:
        outcome = await DeleteOutcome.from_subscriptions(
            self._delete_subscription_ids(
                self._subscription_repo.subscriptions_by_agreement(agreement_id)
            )
        )
        if self._dry_run:
            agreement_deleted = 0
        else:
            agreement_deleted = await self._agreement_repo.delete(agreement_id=agreement_id)
        if not outcome.subscriptions and agreement_deleted == 0:
            return DeleteOutcome()
        self._statement_agreements |= frozenset((agreement_id,))
        agreements = [agreement_id] if agreement_deleted > 0 else []
        return DeleteOutcome(subscriptions=outcome.subscriptions, agreements=agreements)

    async def _delete_query(self, query: RQLQuery) -> DeleteOutcome:
        subscriptions: list[str] = []
        agreements: list[str] = []
        async for agreement_id in self._agreement_ids(query):
            outcome = await self._delete_agreement(agreement_id)
            subscriptions.extend(outcome.subscriptions)
            agreements.extend(outcome.agreements)
        return DeleteOutcome(subscriptions=subscriptions, agreements=agreements)

    async def _delete_subscription_ids(
        self, subscriptions: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        async for subscription_id in subscriptions:
            if self._dry_run or await self._subscription_repo.delete(
                subscription_id=subscription_id
            ):
                yield subscription_id

    async def _has_subscription(self, subscription_id: str) -> bool:
        async for stored_subscription_id in self._subscription_repo.subscriptions_by_agreement():
            if stored_subscription_id == subscription_id:
                return True
        return False

    async def _agreement_ids(self, query: RQLQuery) -> AsyncIterator[str]:
        """Stream the distinct agreement ids of the subscriptions matching the query."""
        seen: set[str] = set()
        try:
            async for sub in self._subscriptions.filter(query).select("agreement.id").iterate():
                agreement = getattr(sub, "agreement", None)
                agreement_id = getattr(agreement, "id", None)
                if isinstance(agreement_id, str) and agreement_id not in seen:
                    seen.add(agreement_id)
                    yield agreement_id
        except MPTError as exc:
            logger.warning("Upstream error resolving agreement ids from subscriptions: %s", exc)
            raise UpstreamSubscriptionError(
                "Failed to resolve agreement ids from subscriptions"
            ) from exc
