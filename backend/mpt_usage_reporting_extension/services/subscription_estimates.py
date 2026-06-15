import asyncio
import datetime as dt
import logging
from dataclasses import dataclass

import typer
from mpt_api_client.exceptions import MPTError
from mpt_extension_sdk.services.mpt_api_service.subscription import SubscriptionService
from rich.console import Console
from rich.table import Table

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.persistence.models import PriceEstimate
from mpt_usage_reporting_extension.persistence.protocols import (
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.types import Month, Year

logger = logging.getLogger(__name__)

_SYNTHETIC_PREFIX = "agreement_additional_"
_PRICE_KEYS = ("PPxM", "SPxM", "PPxY", "SPxY")
_REPORT_HEADERS = ("Subscription ID", *_PRICE_KEYS, "Status")
_MISSING = "—"
_DEFAULT_MAX_CONCURRENCY = 10


@dataclass(frozen=True, slots=True)
class EstimateUpdateOutcome:
    """The result of updating one subscription's estimate, with failure detail if any."""

    subscription_id: str
    agreement_id: str
    year: Year
    month: Month
    estimate: PriceEstimate | None
    failed: bool
    error: str | None = None
    exception: BaseException | None = None


def _is_updatable(bucket: ChargeAccumulation) -> bool:
    """A real (non-synthetic) bucket with a resolved month is eligible for an estimate update."""
    return (
        not bucket.subscription_id.startswith(_SYNTHETIC_PREFIX)
        and bucket.year is not None
        and bucket.month is not None
    )


def _updatable_subscriptions(accumulations: list[ChargeAccumulation]) -> dict[str, str]:
    """Map each real, updatable subscription id in the run to its agreement id."""
    return {
        bucket.subscription_id: bucket.agreement_id
        for bucket in accumulations
        if _is_updatable(bucket)
    }


def _period(outcome: EstimateUpdateOutcome) -> str:
    """Format an outcome's anchor month as ``YYYY-MM``."""
    year = outcome.year
    month = str(outcome.month).zfill(2)
    return f"{year}-{month}"


class SubscriptionEstimateReport:
    """Render the outcome of updating subscription estimates as a console table."""

    def __init__(self, outcomes: list[EstimateUpdateOutcome]) -> None:
        self._outcomes = outcomes

    def render(self) -> None:
        """Print the summary line and, when anything was updated, the per-subscription table."""
        typer.echo(self._summary())
        if self._outcomes:
            Console().print(self._table())

    def _summary(self) -> str:
        ok = sum(not outcome.failed for outcome in self._outcomes)
        failed = len(self._outcomes) - ok
        if not self._outcomes:
            return "Updated estimates to 0 subscription(s), 0 failed"
        period = _period(self._outcomes[0])
        return f"Updated estimates for {period} to {ok} subscription(s), {failed} failed"

    def _table(self) -> Table:
        table = Table(*_REPORT_HEADERS)
        for outcome in self._outcomes:
            status = "FAILED" if outcome.failed else "OK"
            table.add_row(outcome.subscription_id, *self._prices(outcome), status)
        return table

    def _prices(self, outcome: EstimateUpdateOutcome) -> tuple[str, ...]:
        if outcome.estimate is None:
            return (_MISSING,) * len(_PRICE_KEYS)
        prices = outcome.estimate.to_dict()
        return tuple(str(prices[key]) for key in _PRICE_KEYS)


class SubscriptionEstimateUpdater:
    """PUT computed monthly/annual price estimates to each real subscription."""

    def __init__(
        self,
        subscription_repo: SubscriptionAccumulationRepository,
        subscriptions: SubscriptionService,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self._subscription_repo = subscription_repo
        self._subscriptions = subscriptions
        self._max_concurrency = max_concurrency

    async def update(self, accumulations: list[ChargeAccumulation]) -> None:
        """Update every real subscription in the run, report, and exit non-zero on failure."""
        outcomes = await self._update_all(accumulations)
        SubscriptionEstimateReport(outcomes).render()
        failed = [outcome for outcome in outcomes if outcome.failed]
        if failed:
            logger.error(
                "Failed to update %s subscription(s): %s",
                len(failed),
                [
                    (outcome.subscription_id, outcome.agreement_id, outcome.error)
                    for outcome in failed
                ],
            )
            raise typer.Exit(code=1)

    async def _update_all(
        self, accumulations: list[ChargeAccumulation]
    ) -> list[EstimateUpdateOutcome]:
        """Update every real subscription with bounded concurrency; one outcome per subscription."""
        subscriptions = sorted(_updatable_subscriptions(accumulations).items())
        logger.info("Updating estimates to %d subscription(s)", len(subscriptions))
        anchor = dt.datetime.now(tz=dt.UTC).date()
        semaphore = asyncio.Semaphore(self._max_concurrency)
        return await self._run_updates(subscriptions, anchor, semaphore)

    async def _run_updates(
        self,
        subscriptions: list[tuple[str, str]],
        anchor: dt.date,
        semaphore: asyncio.Semaphore,
    ) -> list[EstimateUpdateOutcome]:
        """Resolve every subscription inside a task group, capped by the semaphore."""
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(self._resolve(subscription_id, agreement_id, anchor, semaphore))
                for subscription_id, agreement_id in subscriptions
            ]
        return [task.result() for task in tasks]

    async def _resolve(
        self,
        subscription_id: str,
        agreement_id: str,
        anchor: dt.date,
        semaphore: asyncio.Semaphore,
    ) -> EstimateUpdateOutcome:
        """PUT one subscription's price estimate under the concurrency cap; return its outcome."""
        year, month = anchor.year, Month(anchor.month)
        async with semaphore:
            try:
                estimate = await self._put(subscription_id, year, month)
            except MPTError as exc:
                logger.exception("Failed to update subscription %s", subscription_id)
                return self._failed(subscription_id, agreement_id, year, month, exc)
            except Exception as exc:  # isolate this subscription's failure from the rest
                logger.exception("Unexpected error updating subscription %s", subscription_id)
                return self._failed(subscription_id, agreement_id, year, month, exc)
            return EstimateUpdateOutcome(
                subscription_id, agreement_id, year, month, estimate=estimate, failed=False
            )

    async def _put(self, subscription_id: str, year: Year, month: Month) -> PriceEstimate:
        estimate = await self._subscription_repo.estimate(subscription_id, year, month)
        await self._subscriptions.update(subscription_id, {"price": estimate.to_dict()})
        return estimate

    def _failed(
        self,
        subscription_id: str,
        agreement_id: str,
        year: Year,
        month: Month,
        exc: BaseException,
    ) -> EstimateUpdateOutcome:
        return EstimateUpdateOutcome(
            subscription_id,
            agreement_id,
            year,
            month,
            estimate=None,
            failed=True,
            error=str(exc),
            exception=exc,
        )
