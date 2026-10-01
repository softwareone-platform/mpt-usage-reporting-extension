from collections.abc import AsyncIterator, Iterable, Mapping

from mpt_api_client import RQLQuery
from mpt_api_client.exceptions import MPTError
from mpt_api_client.resources.commerce.agreements import AsyncAgreementsService

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation, read_path
from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.exceptions import EstimateCurrencyError, UpstreamAPIError

_PRICE_CURRENCY = "price.currency"
_BILLING_CURRENCY = "price.billing_currency"
_PRODUCT_ID = "product.id"
# RQL select adds to the default fields; "-*" drops them first, so only these fields come back
_AGREEMENT_PRICE_FIELDS = ("-*", "id", "price")


async def split_currency_agreement_ids(
    agreements: AsyncAgreementsService, product_ids: Iterable[str]
) -> AsyncIterator[str]:
    """Yield the id of each agreement of the products that bills in a currency it is not priced in.

    Such an agreement's charges carry a ``BSPx1`` (price currency) that differs from ``SPx1``
    (billing currency); everywhere else the two are equal, so only these agreements' buckets
    change when the summed field changes. One ``in`` query covers every product.

    Raises:
        UpstreamAPIError: listing the products' agreements failed upstream.
    """
    query = RQLQuery().n(_PRODUCT_ID).in_(list(product_ids))
    try:
        async for agreement in agreements.filter(query).select(*_AGREEMENT_PRICE_FIELDS).iterate():
            if _bills_in_another_currency(agreement):
                yield str(agreement.id)
    except MPTError as exc:
        raise UpstreamAPIError("Failed to list the products' agreements") from exc


def charged_currencies(
    accumulations: Iterable[ChargeAccumulation],
) -> dict[str, frozenset[str]]:
    """Map each real subscription id to the purchase currencies its charges were priced in.

    Only subscriptions whose charges carried a purchase currency appear, so the map is empty
    when no charges were streamed (the ``push-estimates`` commands) and the guard stays off.
    """
    charged: dict[str, set[str]] = {}
    for bucket in accumulations:
        if bucket.currencies and not bucket.subscription_id.startswith(ADDITIONAL_AGREEMENT_PREFIX):
            charged.setdefault(bucket.subscription_id, set()).update(bucket.currencies)
    return {subscription_id: frozenset(seen) for subscription_id, seen in charged.items()}


def _bills_in_another_currency(agreement: object) -> bool:
    """Whether the agreement carries both currencies and they differ."""
    price_currency = read_path(agreement, _PRICE_CURRENCY)
    billing_currency = read_path(agreement, _BILLING_CURRENCY)
    return bool(price_currency and billing_currency and price_currency != billing_currency)


class EstimateCurrencyGuard:
    """Refuse an estimate summed from charges priced in more than one currency.

    ``charged`` maps a subscription id to the purchase currencies of the charges accumulated for
    it in this run (see :func:`charged_currencies`). The summed ``BSPx1`` figures are
    denominated in those currencies; a sum over two currencies means nothing, so the estimate is
    refused instead of uploaded. A single currency is not compared with the agreement: the
    platform derives both the charges' purchase currency and the subscription's price currency
    from the same authorization, so the check makes no API calls.
    """

    def __init__(self, charged: Mapping[str, frozenset[str]] | None = None) -> None:
        self._charged = charged or {}

    def verify(self, subscription_id: str) -> None:
        """Check that the subscription's charges in this run share one purchase currency.

        Raises:
            EstimateCurrencyError: the subscription's charges carry more than one currency.
        """
        charged = self._charged.get(subscription_id, frozenset())
        if len(charged) > 1:
            charged_label = ",".join(sorted(charged))
            raise EstimateCurrencyError(
                f"Subscription {subscription_id} charges are priced in more than one "
                f"currency ({charged_label}), so their sum has no single currency"
            )
