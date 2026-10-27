import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Sequence
from dataclasses import dataclass

from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService

from mpt_usage_reporting_extension.services.helper import as_async_iterator, chunked

logger = logging.getLogger(__name__)

WRITABLE_SUBSCRIPTION_STATUSES = ("Active", "Suspended")
_DEFAULT_CHUNK_SIZE = 100


@dataclass(frozen=True, slots=True)
class StatusCheck:
    """Whether one subscription may receive an estimate, or why that could not be told."""

    subscription_id: str
    writable: bool
    error: MPTError | None = None


class WritableSubscriptionFilter:
    """Tell which subscriptions may receive an estimate: only Active or Suspended ones.

    Operations writes a subscription only while it is Active or Suspended (canon BR-013).
    Terminated and Expired are terminal, and the transient states resolve on their own, so
    every other status is left untouched.
    """

    def __init__(
        self, subscriptions: AsyncSubscriptionsService, chunk_size: int = _DEFAULT_CHUNK_SIZE
    ) -> None:
        self._subscriptions = subscriptions
        self._chunk_size = chunk_size

    async def check(
        self, subscription_ids: AsyncIterable[str] | Iterable[str]
    ) -> AsyncIterator[StatusCheck]:
        """Yield one check per id, listing the ids' statuses one chunk at a time.

        An id the listing does not return - another status, or no such subscription - is not
        writable. When a chunk's listing fails, each of its ids carries the error instead.
        """
        async for chunk in chunked(as_async_iterator(subscription_ids), self._chunk_size):
            try:
                writable = await self._writable(chunk)
            except MPTError as exc:
                logger.exception("Failed to read the status of %d subscription(s)", len(chunk))
                for subscription_id in chunk:
                    yield StatusCheck(subscription_id, writable=False, error=exc)
                continue
            for subscription_id in chunk:
                yield StatusCheck(subscription_id, writable=subscription_id in writable)

    async def _writable(self, chunk: Sequence[str]) -> frozenset[str]:
        """The chunk's ids whose subscription is Active or Suspended, in one RQL listing."""
        in_chunk = RQLQuery().n("id").in_(list(chunk))
        in_writable_status = RQLQuery().n("status").in_(list(WRITABLE_SUBSCRIPTION_STATUSES))
        listing = self._subscriptions.filter(in_chunk & in_writable_status).select("id").iterate()
        writable_ids = {subscription.id async for subscription in listing}
        return frozenset(writable_ids)
