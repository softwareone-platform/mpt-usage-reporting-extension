import asyncio
import datetime as dt
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any

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
from mpt_usage_reporting_extension.types import Month, Year
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


class _ProductTargetResolver:
    """Push the product's API subscriptions that have a stored anchor bucket."""

    async def candidates(
        self,
        selector: ProductSelector,
        repo: SubscriptionAccumulationRepository,
        subscriptions: AsyncSubscriptionsService,
        year: Year,
        month: Month,
    ) -> AsyncIterator[str]:
        """Stream the product's stored subscription ids."""
        query = RQLQuery().n("product.id").eq(selector.product_id)
        async for sub in subscriptions.filter(query).select("id").iterate():
            if await repo.get(subscription_id=sub.id, year=year, month=month) is not None:
                yield sub.id


class _SellerTargetResolver:
    """Push the seller's API subscriptions that have a stored anchor bucket."""

    async def candidates(
        self,
        selector: SellerSelector,
        repo: SubscriptionAccumulationRepository,
        subscriptions: AsyncSubscriptionsService,
        year: Year,
        month: Month,
    ) -> AsyncIterator[str]:
        """Stream the seller's stored subscription ids."""
        query = RQLQuery().n("seller.id").eq(selector.seller_id)
        async for sub in subscriptions.filter(query).select("id").iterate():
            if await repo.get(subscription_id=sub.id, year=year, month=month) is not None:
                yield sub.id


class _SubscriptionTargetResolver:
    """Push a single subscription when it has a stored anchor bucket."""

    async def candidates(
        self,
        selector: SubscriptionSelector,
        repo: SubscriptionAccumulationRepository,
        subscriptions: AsyncSubscriptionsService,
        year: Year,
        month: Month,
    ) -> AsyncIterator[str]:
        """Yield the single subscription id, or nothing when it is not stored."""
        if await repo.get(subscription_id=selector.subscription_id, year=year, month=month):
            yield selector.subscription_id


class _AgreementTargetResolver:
    """Push every stored subscription of one agreement."""

    async def candidates(
        self,
        selector: AgreementSelector,
        repo: SubscriptionAccumulationRepository,
        subscriptions: AsyncSubscriptionsService,
        year: Year,
        month: Month,
    ) -> AsyncIterator[str]:
        """Stream the agreement's stored subscription ids."""
        async for subscription_id in repo.subscriptions_by_agreement(selector.agreement_id):
            yield subscription_id


_RESOLVERS: Mapping[type[Selector], Any] = MappingProxyType({
    ProductSelector: _ProductTargetResolver(),
    SellerSelector: _SellerTargetResolver(),
    SubscriptionSelector: _SubscriptionTargetResolver(),
    AgreementSelector: _AgreementTargetResolver(),
})


async def resolve_targets(
    selector: Selector,
    repo: SubscriptionAccumulationRepository,
    subscriptions: AsyncSubscriptionsService,
    *,
    year: Year,
    month: Month,
) -> AsyncIterator[str]:
    """Dispatch to the selector's resolver and drop synthetic (agreement-additional) ids."""
    candidates = _RESOLVERS[type(selector)].candidates(selector, repo, subscriptions, year, month)
    async for resolved_id in candidates:
        if not resolved_id.startswith(ADDITIONAL_AGREEMENT_PREFIX):
            yield resolved_id


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
            selector,
            repo,
            api_service.client.commerce.subscriptions,
            year=anchor.year,
            month=Month(anchor.month),
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
