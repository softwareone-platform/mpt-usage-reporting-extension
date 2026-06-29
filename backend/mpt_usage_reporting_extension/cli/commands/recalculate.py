import asyncio
import datetime as dt
from typing import Annotated

import typer

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.selectors import build_optional_selector
from mpt_usage_reporting_extension.settings import ExtensionSettings
from mpt_usage_reporting_extension.utils import to_date  # noqa: WPS347
from mpt_usage_reporting_extension.window import resolve_window


def recalculate(  # noqa: WPS211
    from_date: Annotated[
        dt.datetime,
        typer.Option("--from-date", formats=["%Y-%m-%d"], help="Window start day, UTC inclusive."),
    ],
    till_date: Annotated[
        dt.datetime,
        typer.Option("--till-date", formats=["%Y-%m-%d"], help="Window end day, UTC inclusive."),
    ],
    product_id: Annotated[
        str | None,
        typer.Option("--product-id", help="Reprocess one product (defaults to all configured)."),
    ] = None,
    agreement_id: Annotated[
        str | None,
        typer.Option("--agreement-id", help="Reprocess one agreement."),
    ] = None,
    subscription_id: Annotated[
        str | None,
        typer.Option("--subscription-id", help="Reprocess one subscription."),
    ] = None,
    seller_id: Annotated[
        str | None,
        typer.Option("--seller-id", help="Reprocess one seller."),
    ] = None,
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--dry-run",
            help="Preview the command without deleting, persisting, pushing estimates, or cleanup.",
        ),
    ] = False,
) -> None:
    """Delete the scope's buckets, then re-accumulate exactly what was reset."""
    window = resolve_window(from_date=to_date(from_date), till_date=to_date(till_date))
    scope = build_optional_selector(
        product_id=product_id,
        agreement_id=agreement_id,
        subscription_id=subscription_id,
        seller_id=seller_id,
    )
    settings = ExtensionSettings.load()
    ctx = RunContext(
        api_service=build_service(),
        window=window,
        product_ids=(product_id,) if product_id else settings.product_ids,
        seller_id=seller_id or "",
    )
    parameters = {"product_id": product_id, "seller_id": seller_id}
    asyncio.run(UsageReportingPipeline(ctx).recalculate(scope, parameters, dry_run=dry_run))
