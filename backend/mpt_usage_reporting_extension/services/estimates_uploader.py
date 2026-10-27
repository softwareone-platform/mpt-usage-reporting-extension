import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal

from mpt_api_client.exceptions import MPTAPIError
from mpt_api_client.resources.commerce.subscriptions import AsyncSubscriptionsService
from mpt_extension_sdk.observability import trace_span
from mpt_extension_sdk.services.mpt_api_service.subscription import SubscriptionService

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation
from mpt_usage_reporting_extension.constants import ADDITIONAL_AGREEMENT_PREFIX
from mpt_usage_reporting_extension.exceptions import EstimateCurrencyError
from mpt_usage_reporting_extension.persistence.models import (
    PriceEstimate,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.persistence.protocols import (
    SubscriptionAccumulationRepository,
)
from mpt_usage_reporting_extension.services.estimate_currency import EstimateCurrencyGuard
from mpt_usage_reporting_extension.services.helper import as_async_iterator
from mpt_usage_reporting_extension.services.subscription_status import (
    WritableSubscriptionFilter,
)
from mpt_usage_reporting_extension.types import Month, Year
from mpt_usage_reporting_extension.utils import month_ordinal, sanitize_log_value

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENCY = 10
_PRICE_KEYS = ("PPxM", "SPxM", "PPxY", "SPxY")
_MONTHS_PER_YEAR = 12
_ROLLING_MONTHS = 12
_SKIPPED_REASON = "not Active or Suspended"


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
    skipped: bool = False
    estimate: PriceEstimate | None = None
    exception: BaseException | None = None
    error: str | None = None
    dry_run: bool = False

    def line(self) -> str:
        """The single log line summarizing this outcome.

        Every value the caller does not control - the subscription id and the upstream error
        text - goes through :func:`sanitize_log_value`, so neither can forge a report line.
        """
        subscription = sanitize_log_value(self.subscription_id)
        if self.skipped:
            return f'subscription={subscription} status=SKIPPED reason="{_SKIPPED_REASON}"'
        if self.failed or self.estimate is None:
            return f"subscription={subscription} status=FAILED{self._detail()}"
        prices = self.estimate.to_dict()
        labels = (f"{key}={self._price_label(prices[key])}" for key in _PRICE_KEYS)
        body = " ".join(labels)
        status = "DRY-RUN" if self.dry_run else "OK"
        return f"subscription={subscription} {body} status={status}"

    def _detail(self) -> str:
        """The failure reason appended to a failed outcome's line, empty when there is none."""
        return f" error={sanitize_log_value(self.error)}" if self.error else ""

    @staticmethod
    def _price_label(amount: float | None) -> str:  # noqa: WPS602
        """Render one price for the log line, showing absent figures as ``null``."""
        return "null" if amount is None else f"{amount:.4f}"


class EstimateUploadReport:
    """Log each upload outcome as it arrives and tally running counts.

    Only running counts are retained, so a run of any size stays within constant memory.
    """

    def __init__(self, year: Year, month: Month, *, dry_run: bool = False) -> None:
        month_label = str(month).zfill(2)
        self._period = f"{year}-{month_label}"
        self._ok = 0
        self._skipped = 0
        self._failed = 0
        self._dry_run = dry_run

    def record(self, outcome: UploadOutcome) -> None:
        """Log the outcome's line and tally it."""
        if outcome.skipped:
            self._skipped += 1
        elif outcome.failed:
            self._failed += 1
        else:
            self._ok += 1
        logger.info(outcome.line())

    def render(self) -> None:
        """Log the closing summary line for the run."""
        logger.info(self._summary())

    @property
    def has_failures(self) -> bool:
        """Whether any subscription failed to upload."""
        return self._failed > 0

    @property
    def failed_count(self) -> int:
        """How many subscriptions failed to upload."""
        return self._failed

    def _summary(self) -> str:
        verb = "Dry-run" if self._dry_run else "Uploaded"
        period = f" for {self._period}" if self._ok or self._skipped or self._failed else ""
        return (
            f"{verb} estimates{period} to {self._ok} subscription(s), "
            f"{self._skipped} skipped, {self._failed} failed"
        )


class _EstimateCalculator:
    """Compute a subscription's PriceEstimate from its trailing-12-month accumulation buckets."""

    def __init__(self, accumulations: SubscriptionAccumulationRepository) -> None:
        self._accumulations = accumulations

    async def estimate(self, subscription_id: str, year: Year, month: Month) -> PriceEstimate:
        """Fold the trailing-12-month buckets into a PriceEstimate.

        Each month in the window is read with an indexed point lookup; absent months are skipped.
        The monthly pair comes from the anchor month, or - while the anchor month is still being
        billed - from the most recent earlier month in the window that has data, so a subscription
        keeps showing an estimate instead of dropping to null. Only a window with no data at all
        yields null monthly figures, never a fabricated zero. The whole window drives PPxY/SPxY.
        Credits can push a sum below zero, which MPT rejects, so each present price is floored at 0.
        """
        window = await self._fetch_window(subscription_id, year, month)
        rows = sorted((bucket for bucket in window if bucket), key=self._ordinal)
        latest = rows[-1] if rows else None
        self._report_carry_forward(subscription_id, latest, year, month)
        return PriceEstimate(
            ppxm=self._non_negative_sum((latest.ppx1,)) if latest else None,
            spxm=self._non_negative_sum((latest.spx1,)) if latest else None,
            ppxy=self._non_negative_sum(bucket.ppx1 for bucket in rows) if rows else None,
            spxy=self._non_negative_sum(bucket.spx1 for bucket in rows) if rows else None,
        )

    @staticmethod
    def _ordinal(bucket: SubscriptionMonthlyAccumulation) -> int:  # noqa: WPS602
        """Sort key ordering buckets by billing month, most recent last."""
        return month_ordinal(bucket.year, bucket.month)

    @staticmethod
    def _non_negative_sum(amounts: Iterable[Decimal]) -> Decimal:  # noqa: WPS602
        """Sum the amounts, flooring the total at 0."""
        return max(sum(amounts, Decimal(0)), Decimal(0))

    @staticmethod
    def _report_carry_forward(  # noqa: WPS602
        subscription_id: str,
        latest: SubscriptionMonthlyAccumulation | None,
        year: Year,
        month: Month,
    ) -> None:
        """Log that an older month's figures stand in for an anchor month with no buckets."""
        if latest is None or (latest.year, latest.month) == (year, month):
            return
        logger.info(
            "Carrying subscription %s monthly estimate forward from %d-%02d "
            "(anchor %d-%02d has no buckets)",
            sanitize_log_value(subscription_id),
            latest.year,
            latest.month,
            year,
            month,
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
    """Check one produced estimate's currency and PUT it to MPT; never raises.

    An estimate summed from charges priced in more than one currency is refused - reported as a
    failure - rather than uploaded as a meaningless mixed-currency figure.
    """

    def __init__(
        self,
        subscriptions: SubscriptionService,
        guard: EstimateCurrencyGuard | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._subscriptions = subscriptions
        self._guard = guard or EstimateCurrencyGuard()
        self._dry_run = dry_run

    async def consume(self, subscription_id: str, estimate: PriceEstimate) -> UploadOutcome:
        """Check and PUT the estimate; on any error log it and return a failure outcome."""
        try:
            self._guard.verify(subscription_id)
        except EstimateCurrencyError as exc:
            logger.error(  # noqa: TRY400  # a refusal, not a crash: no traceback to report
                "Refusing to upload subscription %s estimate: %s",
                sanitize_log_value(subscription_id),
                sanitize_log_value(str(exc)),
            )
            return UploadOutcome(subscription_id, failed=True, exception=exc, error=str(exc))
        if self._dry_run:
            return UploadOutcome(subscription_id, estimate=estimate, dry_run=True)
        return await self._put(subscription_id, estimate)

    async def _put(self, subscription_id: str, estimate: PriceEstimate) -> UploadOutcome:
        """PUT the estimate and return the outcome."""
        payload = {"price": estimate.to_sales_dict()}
        logger.info("PUT subscription %s price %s", sanitize_log_value(subscription_id), payload)
        try:
            await self._subscriptions.update(subscription_id, payload)
        except MPTAPIError as exc:
            logger.exception(
                "MPT API rejected subscription %s upload with payload %s. \n Reason: %s \n %s",
                subscription_id,
                payload,
                exc.title,
                exc.detail,
            )
            return UploadOutcome(subscription_id, failed=True, exception=exc, error=str(exc))
        except Exception as exc:
            logger.exception(
                "Unexpected error uploading subscription %s with payload %s",
                subscription_id,
                payload,
            )
            return UploadOutcome(subscription_id, failed=True, exception=exc, error=str(exc))
        return UploadOutcome(subscription_id, estimate=estimate)


class EstimatesUploader:
    """Link the estimate producer to the upload consumer, bounded to max_concurrency in flight."""

    def __init__(
        self,
        accumulations: SubscriptionAccumulationRepository,
        subscriptions: SubscriptionService,
        subscription_listing: AsyncSubscriptionsService,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
        *,
        dry_run: bool = False,
    ) -> None:
        self._producer = PriceEstimateProducer(_EstimateCalculator(accumulations))
        self._subscriptions = subscriptions
        self._writable_filter = WritableSubscriptionFilter(subscription_listing)
        self._dry_run = dry_run
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @trace_span(
        "usage_reporting.upload_estimates",
        attributes={
            "usage_reporting.year": lambda uploader, subscription_ids, year, month, **kwargs: int(
                year
            ),
            "usage_reporting.month": lambda uploader, subscription_ids, year, month, **kwargs: int(
                month
            ),
        },
    )
    async def update(
        self,
        subscription_ids: AsyncIterable[str] | Iterable[str],
        year: Year,
        month: Month,
        purchase_currencies: Mapping[str, frozenset[str]] | None = None,
    ) -> EstimateUploadReport:
        """Upload the streamed subscription ids' estimates and return the run report.

        Ids are consumed lazily and a slot is reserved before each task is created, so reads,
        tasks, and uploads stay bounded by ``max_concurrency`` regardless of the id count.
        ``purchase_currencies`` (see ``charged_currencies``) arms the currency guard: an
        estimate is refused when its charges in this run were priced in more than one currency.
        Only Active or Suspended subscriptions are uploaded; the rest are reported as skipped.
        """
        logger.info(
            "Uploading estimates for period %d-%02d%s",
            year,
            month,
            " (dry run)" if self._dry_run else "",
        )
        report = EstimateUploadReport(year, month, dry_run=self._dry_run)
        consumer = PriceEstimateConsumer(
            self._subscriptions, EstimateCurrencyGuard(purchase_currencies), dry_run=self._dry_run
        )
        async with asyncio.TaskGroup() as group:
            async for subscription_id, estimate in self._producer.produce(
                self._writable_ids(subscription_ids, report), year, month
            ):
                await self._semaphore.acquire()
                group.create_task(  # type: ignore[unused-awaitable]  # owned by the group
                    self._record(consumer, report, subscription_id, estimate)
                )
        return report

    async def _writable_ids(
        self,
        subscription_ids: AsyncIterable[str] | Iterable[str],
        report: EstimateUploadReport,
    ) -> AsyncIterator[str]:
        """Yield the ids whose subscription is Active or Suspended.

        Every other id is recorded on the report as skipped. An id whose status could not be
        read is recorded as failed rather than uploaded blind.
        """
        async for check in self._writable_filter.check(subscription_ids):
            if check.writable:
                yield check.subscription_id
            elif check.error is None:
                report.record(UploadOutcome(check.subscription_id, skipped=True))
            else:
                report.record(
                    UploadOutcome(
                        check.subscription_id,
                        failed=True,
                        exception=check.error,
                        error=str(check.error),
                    )
                )

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
