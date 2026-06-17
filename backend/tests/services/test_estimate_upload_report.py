import asyncio
import datetime as dt
from decimal import Decimal

import pytest
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.persistence.models import (
    PriceEstimate,
    SubscriptionMonthlyAccumulation,
)
from mpt_usage_reporting_extension.services.estimates_uploader import (
    EstimatesUploader,
    EstimateUploadReport,
    UploadOutcome,
)
from mpt_usage_reporting_extension.types import Month


class _ConcurrencyTracker:
    """Records the peak number of in-flight calls; ``track`` is an async-mock side effect.

    Each call holds at a barrier sized to the expected cap, so a full batch of concurrent uploads
    must arrive together before any is released — making the observed peak deterministic.
    """

    def __init__(self, parties: int) -> None:
        self.active = 0
        self.peak = 0
        self._gate = asyncio.Barrier(parties)

    async def track(self, *args: object) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await self._gate.wait()
        self.active -= 1


@pytest.fixture
def estimate():
    return PriceEstimate(
        Decimal(5),
        Decimal(6),
        Decimal(50),
        Decimal(60),
    )


@pytest.fixture
def stored_bucket():
    return SubscriptionMonthlyAccumulation(
        subscription_id="SUB",
        agreement_id="AGR",
        year=2026,
        month=Month.JUNE,
        ppx1=Decimal(1),
        spx1=Decimal(1),
        updated_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )


@pytest.fixture
def subscription_repo(mocker, stored_bucket):
    window = {(2026, Month.JUNE): stored_bucket}
    repo = mocker.AsyncMock()
    repo.get.side_effect = lambda subscription_id, year, month: window.get((year, month))
    return repo


@pytest.fixture
def subscriptions(mocker):
    service = mocker.Mock()
    service.update = mocker.AsyncMock()
    return service


def test_report_renders_values_and_statuses(estimate, capsys):
    report = EstimateUploadReport(2026, Month.JUNE)
    report.record(UploadOutcome("SUB-1", estimate=estimate))
    report.record(UploadOutcome("SUB-2", failed=True, error="boom"))

    report.render()  # act

    out = capsys.readouterr().out
    assert "Uploaded estimates for 2026-06 to 1 subscription(s), 1 failed" in out
    assert "SUB-1 PPxM=5.0000 SPxM=6.0000 PPxY=50.0000 SPxY=60.0000 OK" in out
    assert "SUB-2 FAILED: boom" in out


def test_report_summarizes_empty_run(capsys):
    EstimateUploadReport(2026, Month.JUNE).render()  # act

    assert "Uploaded estimates to 0 subscription(s), 0 failed" in capsys.readouterr().out


async def test_update_reports_summary(subscription_repo, subscriptions, capsys):
    uploader = EstimatesUploader(subscription_repo, subscriptions)

    report = await uploader.update(["SUB-1", "SUB-2"], 2026, Month.JUNE)
    report.render()  # act

    assert "to 2 subscription(s), 0 failed" in capsys.readouterr().out


async def test_update_logs_failure_detail(subscription_repo, subscriptions, caplog):
    subscriptions.update.side_effect = MPTError("boom")
    uploader = EstimatesUploader(subscription_repo, subscriptions)

    report = await uploader.update(["SUB-1"], 2026, Month.JUNE)  # act

    assert report.has_failures
    assert "Failed to upload subscription SUB-1" in caplog.text


async def test_update_caps_concurrency(subscription_repo, subscriptions):
    tracker = _ConcurrencyTracker(parties=2)
    subscriptions.update.side_effect = tracker.track
    subscription_ids = [f"SUB-{index}" for index in range(4)]

    updater = EstimatesUploader(subscription_repo, subscriptions, max_concurrency=2)
    await updater.update(subscription_ids, 2026, Month.JUNE)  # act

    assert tracker.peak == 2  # 4 subscriptions, capped at 2 (would be 4 without the bound)
