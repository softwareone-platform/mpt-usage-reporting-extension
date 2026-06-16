import asyncio
import logging
from decimal import Decimal

import pytest
import typer
from mpt_api_client.exceptions import MPTError

from mpt_usage_reporting_extension.persistence.models import PriceEstimate
from mpt_usage_reporting_extension.services.subscription_estimates import (
    EstimateUpdateOutcome,
    SubscriptionEstimateReport,
    SubscriptionEstimateUpdater,
)
from mpt_usage_reporting_extension.types import Month


@pytest.fixture
def estimate():
    return PriceEstimate(
        Decimal(5),
        Decimal(6),
        Decimal(50),
        Decimal(60),
    )


@pytest.fixture
def subscription_repo(mocker, estimate):
    repo = mocker.AsyncMock()
    repo.estimate.return_value = estimate
    return repo


@pytest.fixture
def accumulations(charge_accumulation_factory):
    return [
        charge_accumulation_factory("SUB-1"),
        charge_accumulation_factory("SUB-2"),
    ]


@pytest.fixture
def subscriptions(mocker):
    service = mocker.Mock()
    service.update = mocker.AsyncMock()
    return service


def test_report_renders_values_and_statuses(estimate, capsys):
    outcomes = [
        EstimateUpdateOutcome("SUB-1", "AGR-1", 2026, Month.JUNE, estimate, failed=False),
        EstimateUpdateOutcome(
            "SUB-2",
            "AGR-2",
            2026,
            Month.JUNE,
            None,
            failed=True,
            error="boom",
            exception=MPTError("boom"),
        ),
    ]

    SubscriptionEstimateReport(outcomes).render()  # act

    out = capsys.readouterr().out
    assert "Updated estimates for 2026-06 to 1 subscription(s), 1 failed" in out
    assert "5.0" in out
    assert "OK" in out
    assert "FAILED" in out
    assert "—" in out


def test_report_summarizes_empty_run(capsys):
    SubscriptionEstimateReport([]).render()  # act

    assert "Updated estimates to 0 subscription(s), 0 failed" in capsys.readouterr().out


async def test_push_logs_progress(accumulations, subscription_repo, subscriptions, caplog, capsys):
    caplog.set_level(logging.INFO)

    await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(accumulations)  # act

    assert "Updating estimates to 2 subscription(s)" in caplog.text
    assert "to 2 subscription(s), 0 failed" in capsys.readouterr().out


async def test_push_logs_failure_detail(accumulations, subscription_repo, subscriptions, caplog):
    subscriptions.update.side_effect = MPTError("boom")

    with pytest.raises(typer.Exit):
        await SubscriptionEstimateUpdater(subscription_repo, subscriptions).update(
            accumulations
        )  # act

    logs = caplog.text
    assert "Failed to update subscription" in logs
    assert "Failed to update 2 subscription(s)" in logs
    assert "AGR-1" in logs


class _ConcurrencyTracker:
    """Records the peak number of in-flight calls; ``track`` is an async-mock side effect."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def track(self, *args: object) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0)
        self.active -= 1


async def test_push_caps_concurrency(charge_accumulation_factory, subscription_repo, subscriptions):
    tracker = _ConcurrencyTracker()
    subscriptions.update.side_effect = tracker.track
    accumulations = [charge_accumulation_factory(f"SUB-{index}") for index in range(4)]

    updater = SubscriptionEstimateUpdater(subscription_repo, subscriptions, max_concurrency=2)
    await updater.update(accumulations)  # act

    assert tracker.peak == 2  # 4 subscriptions, capped at 2 (would be 4 without the bound)
