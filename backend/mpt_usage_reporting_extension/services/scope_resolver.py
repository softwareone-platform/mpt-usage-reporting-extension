import logging
from collections.abc import AsyncIterator

from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService

from mpt_usage_reporting_extension.exceptions import UpstreamSubscriptionError
from mpt_usage_reporting_extension.persistence.protocols import SubscriptionAccumulationRepository
from mpt_usage_reporting_extension.selectors import ProductSelector, SellerSelector

logger = logging.getLogger(__name__)

_PRODUCT_ID = "product.id"
_SELLER_ID = "seller.id"


class ScopeResolver:
    """Resolve selector scopes to the target ids their operations act on.

    Owns the commerce-API lookup expanding a product/seller scope into agreement ids and
    the stored-agreement lookup for a subscription scope; callers act on the resolved ids.
    """

    def __init__(
        self,
        subscriptions: AsyncSubscriptionsService,
        subscription_repo: SubscriptionAccumulationRepository,
    ) -> None:
        self._subscriptions = subscriptions
        self._subscription_repo = subscription_repo

    def query_for(self, scope: ProductSelector | SellerSelector) -> RQLQuery:
        """Build the commerce subscriptions query matching the scope."""
        if isinstance(scope, ProductSelector):
            return RQLQuery().n(_PRODUCT_ID).eq(scope.product_id)
        return RQLQuery().n(_SELLER_ID).eq(scope.seller_id)

    def subscription_agreements(self, subscription_id: str) -> AsyncIterator[str]:
        """Stream the stored agreement ids of one subscription."""
        return self._subscription_repo.agreements_by_subscription(subscription_id)

    async def agreement_ids(self, query: RQLQuery) -> AsyncIterator[str]:
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
