import datetime as dt
from decimal import Decimal
from http import HTTPStatus

import pytest
from freezegun import freeze_time
from mpt_extension_sdk.api.context import APIContext
from mpt_extension_sdk.api.errors import APIError

from mpt_usage_reporting_extension.persistence.models import AccumulationPeriod, ExecutionDetail
from mpt_usage_reporting_extension.routers.api import subscriptions
from mpt_usage_reporting_extension.services.recalculate_launcher import (
    RecalculateInProgressError,
)


@pytest.fixture
def subscription_ctx(mocker):
    return mocker.Mock(spec=APIContext)


@pytest.fixture
def execution_detail():
    return ExecutionDetail(
        id=11,
        command="recalculate",
        status="running",
        parameters={"subscription_id": "SUB-1", "trigger": "api"},
        result=None,
        started_at="2026-07-02T10:00:00+00:00",
        completed_at=None,
    )


@pytest.fixture
def launcher(mocker):
    stub = mocker.patch.object(subscriptions, "launcher")
    stub.is_running.return_value = False
    stub.start = mocker.AsyncMock(return_value=11)
    return stub


@pytest.fixture
def database(mocker):
    return mocker.patch.object(subscriptions, "SqliteDatabase").return_value.__aenter__.return_value


@pytest.fixture
def executions(mocker, database, execution_detail):
    repo = mocker.AsyncMock()
    repo.get.return_value = execution_detail
    database.execution_repository = mocker.Mock(return_value=repo)
    return repo


@pytest.fixture
def accumulations(mocker, database):
    periods = [
        AccumulationPeriod(
            subscription_id="SUB-1",
            year=2026,
            month=6,
            ppx1=Decimal("12.50"),
            spx1=Decimal("15.00"),
            updated_at="2026-07-01T09:00:00Z",
        ),
        AccumulationPeriod(
            subscription_id="SUB-1",
            year=2026,
            month=5,
            ppx1=Decimal("7.25"),
            spx1=Decimal("9.10"),
            updated_at="2026-06-01T09:00:00Z",
        ),
    ]

    async def stream(subscription_id):  # noqa: RUF029  # async generator over a list
        for period in periods:
            yield period

    repo = mocker.Mock(periods=stream)
    database.subscription_repository = mocker.Mock(return_value=repo)
    return periods


async def test_recalculate_starts_execution_and_returns_it(
    subscription_ctx, launcher, executions, execution_detail
):
    result = await subscriptions.recalculate_subscription("SUB-1", subscription_ctx)

    launcher.start.assert_awaited_once()
    assert launcher.start.await_args.args[0] == "SUB-1"
    assert result.payload == {
        "id": 11,
        "command": "recalculate",
        "status": "running",
        "parameters": {"subscription_id": "SUB-1", "trigger": "api"},
        "result": None,
        "startedAt": "2026-07-02T10:00:00+00:00",
        "completedAt": None,
    }


@freeze_time("2026-07-02")
async def test_recalculate_uses_last_thirteen_months_window(subscription_ctx, launcher, executions):
    await subscriptions.recalculate_subscription("SUB-1", subscription_ctx)

    window = launcher.start.await_args.args[1]
    assert window.start.date() == dt.date(2025, 7, 1)
    assert window.end.date() == dt.date(2026, 7, 3)


async def test_recalculate_rejects_overlapping_run(subscription_ctx, launcher, executions):
    launcher.start.side_effect = RecalculateInProgressError("SUB-1")

    with pytest.raises(APIError) as exc_info:
        await subscriptions.recalculate_subscription("SUB-1", subscription_ctx)

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert "SUB-1" in exc_info.value.detail


async def test_accumulations_returns_periods(subscription_ctx, accumulations):
    result = await subscriptions.get_accumulations("SUB-1", subscription_ctx)

    assert result.payload == {
        "accumulations": [
            {
                "subscriptionId": "SUB-1",
                "year": 2026,
                "month": 6,
                "ppx1": 12.5,
                "spx1": 15.0,
                "updatedAt": "2026-07-01T09:00:00Z",
            },
            {
                "subscriptionId": "SUB-1",
                "year": 2026,
                "month": 5,
                "ppx1": 7.25,
                "spx1": 9.1,
                "updatedAt": "2026-06-01T09:00:00Z",
            },
        ]
    }


async def test_accumulations_empty_list_is_ok(mocker, subscription_ctx, database):
    stored: list[AccumulationPeriod] = []

    async def stream(subscription_id):  # noqa: RUF029  # async generator over a list
        for period in stored:
            yield period

    database.subscription_repository = mocker.Mock(return_value=mocker.Mock(periods=stream))

    result = await subscriptions.get_accumulations("SUB-1", subscription_ctx)

    assert result.payload == {"accumulations": []}


async def test_recalculate_reads_created_row_by_id(subscription_ctx, launcher, executions):
    await subscriptions.recalculate_subscription("SUB-1", subscription_ctx)

    executions.get.assert_awaited_once_with(11)


@pytest.mark.parametrize(
    ("today", "expected_start"),
    [
        (dt.date(2026, 7, 2), dt.date(2025, 7, 1)),
        (dt.date(2026, 1, 15), dt.date(2025, 1, 1)),
        (dt.date(2026, 12, 31), dt.date(2025, 12, 1)),
    ],
)
def test_recalculate_window_spans_thirteen_months(today, expected_start):
    result = subscriptions._recalculate_window(today)  # noqa: SLF001

    assert result.start.date() == expected_start
    assert result.end.date() == today + dt.timedelta(days=1)
