import asyncio
import datetime as dt
import logging

import typer
from mpt_api_client.exceptions import MPTError
from mpt_extension_sdk.services.mpt_api_service.subscription import SubscriptionService

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.persistence.protocols import (
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.types import Month

logger = logging.getLogger(__name__)

_SYNTHETIC_PREFIX = "agreement_additional_"


def _is_pushable(bucket: ChargeAccumulation) -> bool:
    """A real (non-synthetic) bucket with a resolved month is eligible for an estimate push."""
    return (
        not bucket.subscription_id.startswith(_SYNTHETIC_PREFIX)
        and bucket.year is not None
        and bucket.month is not None
    )


def _real_subscription_ids(ctx: RunContext) -> set[str]:
    """Return the real, persisted subscription ids accumulated this run."""
    totals = ctx.charge_totals
    if totals is None:
        return set()
    return {
        bucket.subscription_id for bucket in totals.accumulations.values() if _is_pushable(bucket)
    }


def _collect_failures(
    subscription_ids: list[str],
    outcomes: list[str | BaseException | None],
) -> list[str]:
    """Map gathered push outcomes to failed ids, logging unexpected (non-MPTError) errors."""
    failed: list[str] = []
    for subscription_id, outcome in zip(subscription_ids, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.error(
                "Unexpected error updating subscription %s", subscription_id, exc_info=outcome
            )
            failed.append(subscription_id)
        elif outcome is not None:
            failed.append(outcome)
    return failed


def _estimate(
    subscription_repo: SubscriptionAccumulationRepository,
    subscription_id: str,
    anchor: dt.date,
) -> dict[str, dict[str, float]]:
    """Build the price payload from the current-month (PPxM) and rolling-year (PPxY) sums."""
    year, month = anchor.year, Month(anchor.month)
    ppxm = subscription_repo.monthly_estimate(subscription_id, year, month)
    ppxy = subscription_repo.yearly_estimate(subscription_id, year, month)
    return {"price": {"PPxM": float(ppxm), "PPxY": float(ppxy)}}


class SubscriptionEstimatePusher:
    """PUT computed monthly/annual price estimates to each real subscription."""

    async def push(
        self,
        ctx: RunContext,
        subscription_repo: SubscriptionAccumulationRepository,
    ) -> None:
        """Update every real subscription touched this run; exit non-zero on any failure."""
        failed = await self._push_all(ctx, subscription_repo)
        if failed:
            logger.error("Failed to update %s subscription(s): %s", len(failed), failed)
            raise typer.Exit(code=1)

    async def _push_all(
        self,
        ctx: RunContext,
        subscription_repo: SubscriptionAccumulationRepository,
    ) -> list[str]:
        """Attempt every real subscription concurrently; return the ids that failed."""
        anchor = dt.datetime.now(tz=dt.UTC).date()
        subscription_ids = sorted(_real_subscription_ids(ctx))
        outcomes = await asyncio.gather(
            *(
                self._push_one(
                    subscription_id, subscription_repo, ctx.api_service.subscriptions, anchor
                )
                for subscription_id in subscription_ids
            ),
            return_exceptions=True,
        )
        return _collect_failures(subscription_ids, outcomes)

    async def _push_one(
        self,
        subscription_id: str,
        subscription_repo: SubscriptionAccumulationRepository,
        subscriptions: SubscriptionService,
        anchor: dt.date,
    ) -> str | None:
        """PUT one subscription's price estimate; return its id on failure, else None."""
        estimate = _estimate(subscription_repo, subscription_id, anchor)
        try:
            await subscriptions.update(subscription_id, estimate)
        except MPTError:
            logger.exception("Failed to update subscription %s", subscription_id)
            return subscription_id
        logger.info("Updated subscription %s estimate", subscription_id)
        return None
