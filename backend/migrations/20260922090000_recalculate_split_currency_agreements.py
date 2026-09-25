"""Rebuild the buckets of agreements billed in a currency other than their price currency."""

import asyncio
import logging
from typing import override

from mpt_tool.migration import DataBaseMigration

from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.exceptions import UpstreamAPIError
from mpt_usage_reporting_extension.mpt_client import build_service
from mpt_usage_reporting_extension.pipeline import UsageReportingPipeline
from mpt_usage_reporting_extension.selectors import AgreementSelector
from mpt_usage_reporting_extension.services.estimate_currency import split_currency_agreement_ids
from mpt_usage_reporting_extension.services.execution_notifier import build_execution_notifier
from mpt_usage_reporting_extension.settings import ExtensionSettings

logger = logging.getLogger(__name__)


class Migration(DataBaseMigration):
    """Re-sum the sales buckets of split-currency agreements in the price currency (MPT-25312).

    Historical buckets summed each charge's ``SPx1``, the sale price in the agreement's billing
    currency, while the subscription's price is denominated in its authorization's currency; the
    sales column now sums ``BSPx1``, the same sale price in that currency. The two fields differ
    only where the agreement bills in a currency other than the one it is priced in ("AWS Vietnam
    USD" prices in USD and bills in VND), so only those agreements are recalculated: each one's
    buckets are deleted, re-filled from its statements, and its estimates re-pushed.
    """

    @override
    def run(self) -> None:
        """Recalculate every split-currency agreement of the configured products, one at a time."""
        self.log.info("Recalculating the agreements billed in a currency other than their price")
        settings = ExtensionSettings.load()
        ctx = RunContext(
            api_service=build_service(),
            window=None,
            product_ids=settings.product_ids,
            notifier=build_execution_notifier(settings),
        )
        asyncio.run(_recalculate_split_currency_agreements(ctx))


async def _recalculate_split_currency_agreements(ctx: RunContext) -> None:
    """Recalculate each split-currency agreement; fail if the listing or any recalculation failed.

    The listing is read to the end before the first recalculation, so paging never interleaves
    with the minutes-long rebuilds. If the listing fails part-way, the agreements it had already
    returned are still recalculated and reported before the listing error is raised; re-running
    the migration is safe because an agreement-scoped recalculate is idempotent.
    """
    agreement_ids, listing_error = await _list_split_currency_agreements(ctx)
    failed = [
        agreement_id
        for agreement_id in agreement_ids
        if not await _recalculate_agreement(ctx, agreement_id)  # noqa: WPS476  # one at a time
    ]
    logger.info(
        "Recalculated %d split-currency agreement(s), %d failed", len(agreement_ids), len(failed)
    )
    if failed:
        failed_label = ", ".join(failed)
        raise RuntimeError(
            f"{len(failed)} agreement recalculation(s) failed: {failed_label}"
        ) from listing_error
    if listing_error is not None:
        raise listing_error


async def _list_split_currency_agreements(
    ctx: RunContext,
) -> tuple[list[str], UpstreamAPIError | None]:
    """Collect the split-currency agreement ids, keeping those found before a listing failure."""
    agreements = ctx.api_service.client.commerce.agreements
    agreement_ids: list[str] = []
    try:
        async for agreement_id in split_currency_agreement_ids(agreements, ctx.product_ids):
            # append per id, not extend(): extend() keeps nothing when the listing fails mid-way
            agreement_ids.append(agreement_id)  # noqa: PERF401
    except UpstreamAPIError as exc:
        logger.exception(
            "Listing split-currency agreements failed after %d agreement(s)", len(agreement_ids)
        )
        return agreement_ids, exc
    logger.info("Found %d split-currency agreement(s) to recalculate", len(agreement_ids))
    return agreement_ids, None


async def _recalculate_agreement(ctx: RunContext, agreement_id: str) -> bool:
    """Recalculate one agreement; report failure instead of raising so the others still run."""
    parameters = {"agreement_id": agreement_id, "reason": "MPT-25312"}
    try:
        await UsageReportingPipeline(ctx).recalculate(AgreementSelector(agreement_id), parameters)
    except Exception:
        # the pipeline already logged and notified this agreement's failure
        logger.exception("Recalculating agreement %s failed", agreement_id)
        return False
    return True
