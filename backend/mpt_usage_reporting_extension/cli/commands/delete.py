import asyncio
from typing import Annotated

import typer
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.persistence.sqlite.database import (
    SqliteDatabase,
    resolve_db_path,
)
from mpt_usage_reporting_extension.selectors import Selector, build_selector
from mpt_usage_reporting_extension.services.bucket_delete import BucketDeleter


def delete(
    product_id: Annotated[
        str | None,
        typer.Option("--product-id", help="Delete every stored bucket of this product."),
    ] = None,
    agreement_id: Annotated[
        str | None,
        typer.Option("--agreement-id", help="Delete every stored bucket of this agreement."),
    ] = None,
    subscription_id: Annotated[
        str | None,
        typer.Option("--subscription-id", help="Delete this single subscription's buckets."),
    ] = None,
    seller_id: Annotated[
        str | None,
        typer.Option("--seller-id", help="Delete every stored bucket of this seller."),
    ] = None,
) -> None:
    """Delete all stored accumulation buckets for one scope."""
    scope = build_selector(
        product_id=product_id,
        agreement_id=agreement_id,
        subscription_id=subscription_id,
        seller_id=seller_id,
    )
    asyncio.run(_delete(build_service(), scope))


async def _delete(api_service: MPTAPIService, scope: Selector) -> None:
    """Open the store and delete the scope's buckets."""
    async with SqliteDatabase(resolve_db_path()) as db:
        await BucketDeleter(
            db.subscription_repository(),
            db.agreement_repository(),
            api_service.client.commerce.subscriptions,
        ).delete(scope)
