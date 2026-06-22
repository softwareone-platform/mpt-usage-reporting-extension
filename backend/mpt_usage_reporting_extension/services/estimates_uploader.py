import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal

from mpt_api_client.exceptions import MPTError
from mpt_extension_sdk.services.mpt_api_service.subscription import SubscriptionService
from rich.console import Console

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.persistence.models import (
    PriceEstimate,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.persistence.protocols import (
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.services.helper import as_async_iterator
from mpt_usage_reporting_extension.types import Month, Year

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENCY = 10
_PRICE_KEYS = ("PPxM", "SPxM", "PPxY", "SPxY")
_MONTHS_PER_YEAR = 12
_ROLLING_MONTHS = 12


def updatable_subscription_ids(
    accumulations: Iterable[ChargeAccumulation],
) -> Iterator[str]:
    """Yield each real, updatable subscription id in the run once (deduped).

    A bucket is updatable when it is real (non-synthetic) and has a resolved year and month.
    """
    seen: set[str] = set()
    for bucket in accumulations:
        updatable = (
            not bucket.subscription_id.startswith(ADDITIONAL_AGREEMENT_PREFIX)
            and bucket.year is not None
            and bucket.month is not None
        )
        if updatable and bucket.subscription_id not in seen:
            seen.add(bucket.subscription_id)
            yield bucket.subscription_id


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """One subscription's upload result, carrying everything needed to print its line."""

    subscription_id: str
    failed: bool = False
    estimate: PriceEstimate | None = None
    exception: BaseException | None = None
    error: str | None = None

    def line(self) -> str:
        """The single console line summarizing this outcome."""
        if self.failed or self.estimate is None:
            detail = f": {self.error}" if self.error else ""
            return f"{self.subscription_id} FAILED{detail}"
        prices = self.estimate.to_dict()
        body = " ".join(f"{key}={prices[key]:.4f}" for key in _PRICE_KEYS)
        return f"{self.subscription_id} {body} OK"


class EstimateUploadReport:
    """Stream upload outcomes to the console and tally running counts.

    Each outcome is printed as one line as it arrives and only running counts are retained, so a
    run of any size stays within constant memory.
    """

    def __init__(self, year: Year, month: Month) -> None:
        month_label = str(month).zfill(2)
        self._period = f"{year}-{month_label}"
        self._console = Console()
        self._ok = 0
        self._failed = 0

    def record(self, outcome: UploadOutcome) -> None:
        """Print the outcome's line and tally it."""
        if outcome.failed:
            self._failed += 1
        else:
            self._ok += 1
        self._console.print(outcome.line())

    def render(self) -> None:
        """Print the closing summary line for the run."""
        self._console.print(self._summary())

    @property
    def has_failures(self) -> bool:
        """Whether any subscription failed to upload."""
        return self._failed > 0

    def _summary(self) -> str:
        if not (self._ok or self._failed):
            return "Uploaded estimates to 0 subscription(s), 0 failed"
        return (
            f"Uploaded estimates for {self._period} to "
            f"{self._ok} subscription(s), {self._failed} failed"
        )


class _EstimateCalculator:
    """Compute a subscription's PriceEstimate from its trailing-12-month accumulation buckets."""

    def __init__(self, accumulations: SubscriptionAccumulationRepository) -> None:
        self._accumulations = accumulations

    async def estimate(self, subscription_id: str, year: Year, month: Month) -> PriceEstimate:
        """Fold the trailing-12-month buckets into a PriceEstimate.

        Each month in the window is read with an indexed point lookup; absent months are skipped.
        The anchor month drives PPxM/SPxM; the whole window drives PPxY/SPxY. An empty window
        yields an all-zero estimate.
        """
        anchor = (year, month)
        window = await self._fetch_window(subscription_id, year, month)
        rows = [bucket for bucket in window if bucket]
        monthly = [bucket for bucket in rows if (bucket.year, bucket.month) == anchor]
        return PriceEstimate(
            ppxm=sum((bucket.ppx1 for bucket in monthly), Decimal(0)),
            spxm=sum((bucket.spx1 for bucket in monthly), Decimal(0)),
            ppxy=sum((bucket.ppx1 for bucket in rows), Decimal(0)),
            spxy=sum((bucket.spx1 for bucket in rows), Decimal(0)),
        )

    async def _fetch_window(
        self, subscription_id: str, year: Year, month: Month
    ) -> list[SubscriptionMonthlyAccumulation | None]:
        """Point-read every month in the trailing window concurrently (absent months are None)."""
        return await asyncio.gather(
            *(
                self._accumulations.get(subscription_id, bucket_year, bucket_month)
                for bucket_year, bucket_month in self._trailing_months(year, month)
            )
        )

    @staticmethod
    def _trailing_months(year: Year, month: Month) -> Iterator[tuple[Year, Month]]:  # noqa: WPS602
        """Yield the 12 (year, month) pairs in the trailing window ending at the anchor month."""
        anchor = year * _MONTHS_PER_YEAR + (month - 1)
        for ordinal in range(anchor - _ROLLING_MONTHS + 1, anchor + 1):
            bucket_year, bucket_index = divmod(ordinal, _MONTHS_PER_YEAR)
            yield bucket_year, Month(bucket_index + 1)


class PriceEstimateProducer:
    """Compute each subscription's estimate, producing (id, estimate) pairs lazily."""

    def __init__(self, calculator: _EstimateCalculator) -> None:
        self._calculator = calculator

    async def produce(
        self,
        subscription_ids: AsyncIterable[str] | Iterable[str],
        year: Year,
        month: Month,
    ) -> AsyncIterator[tuple[str, PriceEstimate]]:
        """Yield (subscription_id, estimate) for each id, computing the estimate from the store."""
        async for subscription_id in as_async_iterator(subscription_ids):
            estimate = await self._calculator.estimate(subscription_id, year, month)
            yield subscription_id, estimate


class PriceEstimateConsumer:
    """PUT one produced price estimate to MPT and return its outcome; never raises."""

    def __init__(self, subscriptions: SubscriptionService) -> None:
        self._subscriptions = subscriptions

    async def consume(self, subscription_id: str, estimate: PriceEstimate) -> UploadOutcome:
        """PUT the estimate and return the outcome; on any error log it and return a failure."""
        try:
            await self._subscriptions.update(subscription_id, {"price": estimate.to_dict()})
        except MPTError as exc:
            logger.exception("Failed to upload subscription %s", subscription_id)
            return UploadOutcome(subscription_id, failed=True, exception=exc, error=str(exc))
        except Exception as exc:  # isolate this subscription's failure from the rest
            logger.exception("Unexpected error uploading subscription %s", subscription_id)
            return UploadOutcome(subscription_id, failed=True, exception=exc, error=str(exc))
        return UploadOutcome(subscription_id, estimate=estimate)


class EstimatesUploader:
    """Link the estimate producer to the upload consumer, bounded to max_concurrency in flight."""

    def __init__(
        self,
        accumulations: SubscriptionAccumulationRepository,
        subscriptions: SubscriptionService,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self._producer = PriceEstimateProducer(_EstimateCalculator(accumulations))
        self._subscriptions = subscriptions
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def update(
        self,
        subscription_ids: AsyncIterable[str] | Iterable[str],
        year: Year,
        month: Month,
    ) -> EstimateUploadReport:
        """Upload the streamed subscription ids' estimates and return the run report.

        Ids are consumed lazily and a slot is reserved before each task is created, so reads,
        tasks, and uploads stay bounded by ``max_concurrency`` regardless of the id count.
        """
        report = EstimateUploadReport(year, month)
        consumer = PriceEstimateConsumer(self._subscriptions)
        async with asyncio.TaskGroup() as group:
            async for subscription_id, estimate in self._producer.produce(
                subscription_ids, year, month
            ):
                await self._semaphore.acquire()
                group.create_task(  # type: ignore[unused-awaitable]  # owned by the group
                    self._record(consumer, report, subscription_id, estimate)
                )
        return report

    async def _record(
        self,
        consumer: PriceEstimateConsumer,
        report: EstimateUploadReport,
        subscription_id: str,
        estimate: PriceEstimate,
    ) -> None:
        """Consume one item, record its outcome, then release the reserved slot."""
        outcome = await consumer.consume(subscription_id, estimate)  # never raises
        report.record(outcome)
        self._semaphore.release()
