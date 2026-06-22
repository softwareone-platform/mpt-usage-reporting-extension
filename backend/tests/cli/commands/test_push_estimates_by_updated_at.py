import datetime as dt
from decimal import Decimal
from itertools import starmap

import pytest

from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.cli.commands.push_estimates_by_updated_at import (
    _updated_subscription_ids,  # noqa: PLC2701
)
from mpt_usage_reporting_extension.persistence.models import SubscriptionMonthlyAccumulation
from mpt_usage_reporting_extension.types import Month


class _StubRepo:
    """Subscription repo stub whose ``updated`` streams the buckets set on it."""

    def __init__(self):
        self.buckets: list[SubscriptionMonthlyAccumulation] = []

    async def updated(self, updated_on):
        for bucket in self.buckets:
            yield bucket


def _bucket(subscription_id, month):
    return SubscriptionMonthlyAccumulation(
        subscription_id=subscription_id,
        agreement_id="AGR-1",
        year=2026,
        month=month,
        ppx1=Decimal(1),
        spx1=Decimal(1),
        updated_at=dt.datetime(2026, 6, 18, tzinfo=dt.UTC),
    )


@pytest.fixture
def repo():
    return _StubRepo()


@pytest.fixture
def updated_at_collaborators(mocker):
    """Patch the service, database, id stream, and uploader for the by-updated-at command."""
    estimates = cli.commands.push_estimates_by_updated_at
    mocker.patch.object(estimates, "build_service")
    database = mocker.MagicMock()
    database.__aenter__ = mocker.AsyncMock(return_value=database)
    database.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch.object(estimates, "resolve_db_path")
    mocker.patch.object(estimates, "SqliteDatabase", return_value=database)
    subscription_ids = mocker.patch.object(estimates, "_updated_subscription_ids")
    uploader = mocker.patch.object(estimates, "EstimatesUploader").return_value
    return subscription_ids, uploader


async def _drain(repo):
    ids = _updated_subscription_ids(repo, dt.date(2026, 6, 18))
    return [subscription_id async for subscription_id in ids]


async def test_yields_distinct_subscription_ids(repo):
    keys = [("SUB-1", 5), ("SUB-1", 6), ("SUB-2", 6)]
    repo.buckets = list(starmap(_bucket, keys))

    result = await _drain(repo)  # act

    assert result == ["SUB-1", "SUB-2"]


async def test_drops_synthetic_agreement_ids(repo):
    repo.buckets = [_bucket("agreement_additional_AGR-9", 6), _bucket("SUB-1", 6)]

    result = await _drain(repo)  # act

    assert result == ["SUB-1"]


async def test_empty_when_nothing_updated(repo):
    result = await _drain(repo)  # act

    assert not result


def test_uploads_anchored_on_last_month(mocker, runner, updated_at_collaborators):
    subscription_ids, uploader = updated_at_collaborators
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=False))
    anchor = dt.date(2026, 5, 31)
    mocker.patch.object(
        cli.commands.push_estimates_by_updated_at, "last_month", return_value=anchor
    )

    result = runner.invoke(
        cli.app, ["push-estimates", "by-updated-at", "--updated-on", "2026-06-18"]
    )

    assert result.exit_code == 0
    uploader.update.assert_awaited_once_with(
        subscription_ids.return_value, anchor.year, Month(anchor.month)
    )


def test_defaults_to_today(mocker, runner, updated_at_collaborators):
    subscription_ids, uploader = updated_at_collaborators
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=False))
    today = dt.datetime.now(tz=dt.UTC).date()

    result = runner.invoke(cli.app, ["push-estimates", "by-updated-at"])  # act

    assert result.exit_code == 0
    subscription_ids.assert_called_once_with(mocker.ANY, today)


def test_exits_on_failure(mocker, runner, updated_at_collaborators):
    _subscription_ids, uploader = updated_at_collaborators
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True))

    result = runner.invoke(
        cli.app, ["push-estimates", "by-updated-at", "--updated-on", "2026-06-18"]
    )

    assert result.exit_code == 1
