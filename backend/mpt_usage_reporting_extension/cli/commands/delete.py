import asyncio
from collections.abc import Mapping
from typing import Annotated

import typer
from mpt_extension_sdk.observability import trace_span
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.persistence.postgres.database import (
    PostgresDatabase,
    resolve_database_url,
)
from mpt_usage_reporting_extension.selectors import Selector, build_selector
from mpt_usage_reporting_extension.services.bucket_delete import (
    BucketDeleter,
    ScopeBucketDeleter,
)
from mpt_usage_reporting_extension.services.execution_tracker import ExecutionTracker
from mpt_usage_reporting_extension.services.scope_resolver import ScopeResolver
from mpt_usage_reporting_extension.types import Command


def delete_command(
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
    parameters = {
        "product_id": product_id,
        "agreement_id": agreement_id,
        "subscription_id": subscription_id,
        "seller_id": seller_id,
    }
    asyncio.run(delete(build_service(), scope, parameters))


@trace_span(
    "usage_reporting.delete",
    attributes={
        "usage_reporting.scope": lambda api_service, scope, parameters: type(scope).__name__,
    },
)
async def delete(
    api_service: MPTAPIService, scope: Selector, parameters: Mapping[str, object]
) -> None:
    """Open the store and delete the scope's buckets."""
    async with PostgresDatabase(resolve_database_url()) as db:
        tracker = ExecutionTracker(db.execution_repository())
        async with tracker.track(Command.DELETE, parameters) as execution:
            outcome = await _build_scope_deleter(api_service, db).delete(scope)
            execution.record_result(
                subscription_deleted=len(outcome.subscriptions),
                agreement_deleted=len(outcome.agreements),
            )


def _build_scope_deleter(api_service: MPTAPIService, db: PostgresDatabase) -> ScopeBucketDeleter:
    """Wire the scope deleter with its bucket deleter and shared resolver."""
    resolver = ScopeResolver(
        api_service.client.commerce.subscriptions, db.subscription_repository()
    )
    return ScopeBucketDeleter(
        BucketDeleter(db.subscription_repository(), db.agreement_repository(), resolver),
        resolver,
    )
