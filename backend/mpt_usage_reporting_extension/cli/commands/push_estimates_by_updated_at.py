import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from typing import Annotated

import typer
from mpt_extension_sdk.services.mpt_api_service import MPTAPIService

from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.persistence.postgres.database import (
    PostgresDatabase,
    resolve_database_url,
)
from mpt_usage_reporting_extension.persistence.protocols import (
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.services.estimates_uploader import EstimatesUploader
from mpt_usage_reporting_extension.types import Month
from mpt_usage_reporting_extension.utils import last_month, to_date  # noqa: WPS347


def push_estimates_by_updated_at(
    updated_on: Annotated[
        dt.datetime | None,
        typer.Option(
            "--updated-on",
            formats=["%Y-%m-%d"],
            help="UTC day the rows were last written (YYYY-MM-DD); defaults to today.",
        ),
    ] = None,
) -> None:
    """Recompute price estimates for subscriptions stored on a given day and upload them."""
    today = dt.datetime.now(tz=dt.UTC).date()
    day = to_date(updated_on) or today
    asyncio.run(_push_estimates_by_updated_at(build_service(), day))


async def _push_estimates_by_updated_at(api_service: MPTAPIService, updated_at: dt.date) -> None:
    """Upload estimates for every subscription whose stored rows were last written on updated_at."""
    anchor = last_month(dt.datetime.now(tz=dt.UTC).date())
    async with PostgresDatabase(resolve_database_url()) as db:
        repo = db.subscription_repository()
        subscription_ids = _updated_subscription_ids(repo, updated_at)
        report = await EstimatesUploader(repo, api_service.subscriptions).update(
            subscription_ids, anchor.year, Month(anchor.month)
        )
    report.render()
    if report.has_failures:
        raise typer.Exit(code=1)


async def _updated_subscription_ids(
    repo: SubscriptionAccumulationRepository, updated_at: dt.date
) -> AsyncIterator[str]:
    """Stream the distinct, real subscription ids whose buckets were last written on updated_at."""
    seen: set[str] = set()
    async for bucket in repo.updated(updated_at):
        subscription_id = bucket.subscription_id
        if subscription_id in seen or subscription_id.startswith(ADDITIONAL_AGREEMENT_PREFIX):
            continue
        seen.add(subscription_id)
        yield subscription_id
