from collections.abc import Sequence

from mpt_api_client import RQLQuery
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService

WRITABLE_SUBSCRIPTION_STATUSES = ("Active", "Suspended")


class SubscriptionStatusLookup:
    """Read subscription statuses by id, so only Active or Suspended ones receive an estimate.

    Operations writes a subscription only while it is Active or Suspended (canon BR-013).
    Terminated and Expired are terminal, and the transient states resolve on their own, so
    every other status is left untouched.
    """

    def __init__(self, subscriptions: AsyncSubscriptionsService) -> None:
        self._subscriptions = subscriptions

    async def statuses(self, subscription_ids: Sequence[str]) -> dict[str, str | None]:
        """Map each given id that has a subscription to its status, in one RQL listing.

        An id missing from the result has no subscription; a status the platform did not send
        is None. Upstream and decoding errors propagate to the caller.
        """
        query = RQLQuery().n("id").in_(list(subscription_ids))
        listing = self._subscriptions.filter(query).select("id", "status").iterate()
        return {subscription.id: subscription.status async for subscription in listing}
