import asyncio
from typing import Annotated

import typer

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.selectors import build_optional_selector
from mpt_usage_reporting_extension.settings import ExtensionSettings


def recalculate(
    product_id: Annotated[
        str | None,
        typer.Option("--product-id", help="Reprocess one product (defaults to all configured)."),
    ] = None,
    seller_id: Annotated[
        str | None,
        typer.Option("--seller-id", help="Reprocess one seller."),
    ] = None,
) -> None:
    """Delete the scope's buckets, then perform a regular run."""
    scope = build_optional_selector(
        product_id=product_id,
        agreement_id=None,
        subscription_id=None,
        seller_id=seller_id,
    )
    settings = ExtensionSettings.load()
    ctx = RunContext(
        api_service=build_service(),
        window=None,
        product_ids=(product_id,) if product_id else settings.product_ids,
        seller_id=seller_id or "",
    )
    parameters = {"product_id": product_id, "seller_id": seller_id}
    asyncio.run(UsageReportingPipeline(ctx).recalculate(scope, parameters))
