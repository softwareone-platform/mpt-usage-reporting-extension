import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, assert_never

import typer
from mpt_api_client import RQLQuery
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.persistence.protocols import (
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.services.estimates_uploader import EstimatesUploader
from mpt_usage_reporting_extension.types import Month
from mpt_usage_reporting_extension.utils import last_month


@dataclass(frozen=True, slots=True)
class ProductSelector:
    """Push every stored subscription of one product."""

    product_id: str


@dataclass(frozen=True, slots=True)
class SellerSelector:
    """Push every stored subscription of one seller."""

    seller_id: str


@dataclass(frozen=True, slots=True)
class SubscriptionSelector:
    """Push one stored subscription."""

    subscription_id: str


@dataclass(frozen=True, slots=True)
class AgreementSelector:
    """Push every stored subscription of one agreement."""

    agreement_id: str


Selector = ProductSelector | SellerSelector | SubscriptionSelector | AgreementSelector


class _ApiTargetResolver:
    """Enumerate the API subscriptions of a product or seller."""

    def __init__(self, query: RQLQuery, subscriptions: AsyncSubscriptionsService) -> None:
        self._query = query
        self._subscriptions = subscriptions

    async def candidates(self) -> AsyncIterator[str]:
        """Stream the matching subscription ids from the API."""
        async for sub in self._subscriptions.filter(self._query).select("id").iterate():
            yield sub.id


class _SubscriptionTargetResolver:
    """Enumerate a single subscription."""

    def __init__(self, subscription_id: str) -> None:
        self._subscription_id = subscription_id

    async def candidates(self) -> AsyncIterator[str]:
        """Yield the single named subscription id."""
        yield self._subscription_id


class _AgreementTargetResolver:
    """Enumerate every stored subscription of one agreement."""

    def __init__(self, agreement_id: str) -> None:
        self._agreement_id = agreement_id

    async def candidates(self, repo: SubscriptionAccumulationRepository) -> AsyncIterator[str]:
        """Stream the agreement's stored subscription ids."""
        async for subscription_id in repo.subscriptions_by_agreement(self._agreement_id):
            yield subscription_id


async def resolve_targets(
    selector: Selector,
    repo: SubscriptionAccumulationRepository,
    subscriptions: AsyncSubscriptionsService,
) -> AsyncIterator[str]:
    """Dispatch to the selector's resolver and drop synthetic (agreement-additional) ids."""
    async for resolved_id in _candidates(selector, repo, subscriptions):
        if not resolved_id.startswith(ADDITIONAL_AGREEMENT_PREFIX):
            yield resolved_id


def _candidates(
    selector: Selector,
    repo: SubscriptionAccumulationRepository,
    subscriptions: AsyncSubscriptionsService,
) -> AsyncIterator[str]:
    """Build the selector's id stream from the resolver that names its subscriptions."""
    match selector:
        case ProductSelector(product_id):
            return _ApiTargetResolver(
                RQLQuery().n("product.id").eq(product_id), subscriptions
            ).candidates()
        case SellerSelector(seller_id):
            return _ApiTargetResolver(
                RQLQuery().n("seller.id").eq(seller_id), subscriptions
            ).candidates()
        case SubscriptionSelector(subscription_id):
            return _SubscriptionTargetResolver(subscription_id).candidates()
        case AgreementSelector(agreement_id):
            return _AgreementTargetResolver(agreement_id).candidates(repo)
    assert_never(selector)  # pragma: no cover


def push_estimates_by_id(
    product_id: Annotated[
        str | None,
        typer.Option("--product-id", help="Push every stored subscription of this product."),
    ] = None,
    agreement_id: Annotated[
        str | None,
        typer.Option("--agreement-id", help="Push every stored subscription of this agreement."),
    ] = None,
    subscription_id: Annotated[
        str | None,
        typer.Option("--subscription-id", help="Push this single subscription."),
    ] = None,
    seller_id: Annotated[
        str | None,
        typer.Option("--seller-id", help="Push every stored subscription of this seller."),
    ] = None,
) -> None:
    """Recompute price estimates from stored usage and upload them — no statement download."""
    selector = _build_selector(
        product_id=product_id,
        agreement_id=agreement_id,
        subscription_id=subscription_id,
        seller_id=seller_id,
    )
    asyncio.run(_push_estimates_by_id(build_service(), selector))


async def _push_estimates_by_id(api_service: MPTAPIService, selector: Selector) -> None:
    """Resolve the selector's subscription ids and upload their estimates."""
    anchor = last_month(dt.datetime.now(tz=dt.UTC).date())
    async with SqliteDatabase(resolve_db_path()) as db:
        repo = db.subscription_repository()
        subscription_ids = resolve_targets(
            selector, repo, api_service.client.commerce.subscriptions
        )
        report = await EstimatesUploader(repo, api_service.subscriptions).update(
            subscription_ids, anchor.year, Month(anchor.month)
        )
    report.render()
    if report.has_failures:
        raise typer.Exit(code=1)


def _build_selector(
    *,
    product_id: str | None,
    agreement_id: str | None,
    subscription_id: str | None,
    seller_id: str | None,
) -> Selector:
    """Build the single target selector from the flags, or fail when the combination is invalid."""
    factories = (
        (product_id, ProductSelector),
        (agreement_id, AgreementSelector),
        (subscription_id, SubscriptionSelector),
        (seller_id, SellerSelector),
    )
    selectors = [build(flag) for flag, build in factories if flag is not None]
    if len(selectors) != 1:
        raise typer.BadParameter(
            "Use exactly one of --product-id / --agreement-id / --subscription-id / --seller-id."
        )
    return selectors[0]
