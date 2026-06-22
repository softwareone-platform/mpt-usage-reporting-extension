import datetime as dt
from types import SimpleNamespace

import pytest
from mpt_api_client import RQLQuery

from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.cli.commands.push_estimates_by_id import (
    AgreementSelector,
    ProductSelector,
    SellerSelector,
    SubscriptionSelector,
    resolve_targets,
)
from mpt_usage_reporting_extension.types import Month


class _StubRepo:
    """Subscription repo stub: tracks stored (subscription_id, agreement_id) pairs."""

    def __init__(self):
        self.stored: list[tuple[str, str]] = []

    async def subscriptions_by_agreement(self, agreement_id=None):
        for subscription_id, agreement in self.stored:
            if agreement_id is None or agreement == agreement_id:
                yield subscription_id


class _StubSubscriptions:
    """commerce.subscriptions stub: records the RQL query and streams the ids set on it."""

    def __init__(self):
        self.ids: list[str] = []
        self.query = None

    def filter(self, query):
        self.query = query
        return self

    def select(self, *fields):
        return self

    async def iterate(self):
        for subscription_id in self.ids:
            yield SimpleNamespace(id=subscription_id)


@pytest.fixture
def repo():
    return _StubRepo()


@pytest.fixture
def subscriptions():
    return _StubSubscriptions()


@pytest.fixture
def sub1():
    return ("SUB-1", "AGR-1")


@pytest.fixture
def sub2():
    return ("SUB-2", "AGR-2")


@pytest.fixture
def push_collaborators(mocker):
    """Patch the service, database, resolver, and uploader; return the resolver/uploader mocks."""
    mocker.patch.object(cli.commands.push_estimates_by_id, "build_service")
    database = mocker.MagicMock()
    database.__aenter__ = mocker.AsyncMock(return_value=database)
    database.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch.object(cli.commands.push_estimates_by_id, "resolve_db_path")
    mocker.patch.object(cli.commands.push_estimates_by_id, "SqliteDatabase", return_value=database)
    estimates = cli.commands.push_estimates_by_id
    resolve_targets = mocker.patch.object(estimates, "resolve_targets")
    uploader = mocker.patch.object(estimates, "EstimatesUploader").return_value
    return resolve_targets, uploader


async def _drain(selector, repo, subscriptions):
    ids = resolve_targets(selector, repo, subscriptions)
    return [subscription_id async for subscription_id in ids]


async def test_resolve_filters_by_subscription_id(repo, subscriptions, sub1, sub2):
    repo.stored = [sub1, sub2]

    result = await _drain(SubscriptionSelector("SUB-2"), repo, subscriptions)  # act

    assert result == ["SUB-2"]


async def test_resolve_subscription_id_unstored(repo, subscriptions):
    result = await _drain(SubscriptionSelector("SUB-MISSING"), repo, subscriptions)  # act

    assert result == ["SUB-MISSING"]


async def test_resolve_filters_by_agreement_id(repo, subscriptions, sub1, sub2):
    repo.stored = [sub1, sub2, ("SUB-3", "AGR-1")]

    result = await _drain(AgreementSelector("AGR-1"), repo, subscriptions)  # act

    assert result == ["SUB-1", "SUB-3"]


async def test_resolve_by_product(repo, subscriptions, sub1, sub2):
    subscriptions.ids = ["SUB-2", "SUB-9"]
    expected_query = RQLQuery().n("product.id").eq("PRD-1")

    result = await _drain(ProductSelector("PRD-1"), repo, subscriptions)  # act

    assert result == ["SUB-2", "SUB-9"]
    assert str(subscriptions.query) == str(expected_query)


async def test_resolve_by_seller(repo, subscriptions, sub1, sub2):
    repo.stored = [sub1, sub2]
    subscriptions.ids = ["SUB-1"]
    expected_query = RQLQuery().n("seller.id").eq("SEL-1")

    result = await _drain(SellerSelector("SEL-1"), repo, subscriptions)  # act

    assert result == ["SUB-1"]
    assert str(subscriptions.query) == str(expected_query)


async def test_resolve_drops_synthetic_ids(repo, subscriptions, sub1):
    repo.stored = [("agreement_additional_AGR-1", "AGR-1"), sub1]

    result = await _drain(AgreementSelector("AGR-1"), repo, subscriptions)  # act

    assert result == ["SUB-1"]


def test_invokes_push_estimates_by_id(mocker, runner):
    mocker.patch.object(cli.commands.push_estimates_by_id, "build_service")
    push = mocker.patch.object(cli.commands.push_estimates_by_id, "_push_estimates_by_id")

    result = runner.invoke(cli.app, ["push-estimates", "by-id", "--subscription-id", "SUB-1"])

    assert result.exit_code == 0
    push.assert_awaited_once()
    assert push.await_args.args[1] == cli.commands.push_estimates_by_id.SubscriptionSelector(
        "SUB-1"
    )


def test_rejects_two_selectors(mocker, runner):
    push = mocker.patch.object(cli.commands.push_estimates_by_id, "_push_estimates_by_id")

    result = runner.invoke(
        cli.app, ["push-estimates", "by-id", "--product-id", "PRD-1", "--seller-id", "SEL-2"]
    )

    assert result.exit_code != 0
    push.assert_not_called()


def test_rejects_no_arguments(mocker, runner):
    push = mocker.patch.object(cli.commands.push_estimates_by_id, "_push_estimates_by_id")

    result = runner.invoke(cli.app, ["push-estimates", "by-id"])

    assert result.exit_code != 0
    push.assert_not_called()


def test_uploads_anchored_on_last_month(mocker, runner, push_collaborators):
    resolve_targets, uploader = push_collaborators
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=False))
    anchor = dt.date(2026, 5, 31)
    mocker.patch.object(cli.commands.push_estimates_by_id, "last_month", return_value=anchor)

    result = runner.invoke(cli.app, ["push-estimates", "by-id", "--subscription-id", "SUB-1"])

    assert result.exit_code == 0
    uploader.update.assert_awaited_once_with(
        resolve_targets.return_value, anchor.year, Month(anchor.month)
    )


def test_exits_on_failure(mocker, runner, push_collaborators):
    _resolve_targets, uploader = push_collaborators
    uploader.update = mocker.AsyncMock(return_value=mocker.Mock(has_failures=True))

    result = runner.invoke(cli.app, ["push-estimates", "by-id", "--subscription-id", "SUB-1"])

    assert result.exit_code == 1
