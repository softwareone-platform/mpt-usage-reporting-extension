import asyncio
import datetime as dt

import pytest

from mpt_usage_reporting_extension.selectors import SubscriptionSelector
from mpt_usage_reporting_extension.services import recalculate_launcher
from mpt_usage_reporting_extension.types import Command, ExecutionStatus
from mpt_usage_reporting_extension.window import RunWindow


@pytest.fixture
def window():
    return RunWindow(
        start=dt.datetime(2025, 6, 1, tzinfo=dt.UTC),
        end=dt.datetime(2026, 7, 3, tzinfo=dt.UTC),
    )


@pytest.fixture
def executions(mocker):
    database = mocker.patch.object(
        recalculate_launcher, "SqliteDatabase"
    ).return_value.__aenter__.return_value
    repo = mocker.AsyncMock()
    repo.start.return_value = 11
    database.execution_repository = mocker.Mock(return_value=repo)
    return repo


@pytest.fixture
def api_service(mocker):
    return mocker.patch.object(recalculate_launcher, "build_service").return_value


@pytest.fixture
def settings(mocker):
    stub = mocker.patch.object(recalculate_launcher, "ExtensionSettings")
    stub.load.return_value.product_ids = ("PRD-1", "PRD-2")
    return stub


@pytest.fixture
def pipeline(mocker):
    stub = mocker.Mock()
    stub.recalculate = mocker.AsyncMock()
    return stub


@pytest.fixture
def launcher(executions, api_service, settings, pipeline):
    return recalculate_launcher.RecalculateLauncher(lambda ctx: pipeline)


async def _drain(launcher, subscription_id):
    while launcher.is_running(subscription_id):
        await asyncio.sleep(0)


async def test_start_opens_execution_row_and_returns_its_id(executions, launcher, window):
    result = await launcher.start("SUB-1", window)

    assert result == 11
    executions.start.assert_awaited_once_with(
        Command.RECALCULATE,
        {
            "subscription_id": "SUB-1",
            "from_date": "2025-06-01",
            "till_date": "2026-07-02",
            "trigger": "api",
        },
    )


async def test_start_runs_pipeline_against_started_row(pipeline, launcher, window):
    result = await launcher.start("SUB-1", window)

    await _drain(launcher, "SUB-1")
    pipeline.recalculate.assert_awaited_once_with(
        SubscriptionSelector("SUB-1"),
        {
            "subscription_id": "SUB-1",
            "from_date": "2025-06-01",
            "till_date": "2026-07-02",
            "trigger": "api",
        },
        execution_id=result,
    )


async def test_start_builds_context_from_configured_products(mocker, api_service, launcher, window):
    contexts = []
    factory_launcher = recalculate_launcher.RecalculateLauncher(
        lambda ctx: contexts.append(ctx) or mocker.Mock(recalculate=mocker.AsyncMock())
    )

    await factory_launcher.start("SUB-1", window)

    await _drain(factory_launcher, "SUB-1")
    assert contexts[0].product_ids == ("PRD-1", "PRD-2")
    assert contexts[0].api_service is api_service
    assert contexts[0].window is window


async def test_is_running_while_task_pending(mocker, pipeline, launcher, window):
    gate = asyncio.Event()

    async def blocked(*args, **kwargs):
        await gate.wait()

    pipeline.recalculate = mocker.AsyncMock(side_effect=blocked)

    await launcher.start("SUB-1", window)

    result = launcher.is_running("SUB-1")

    assert result is True
    gate.set()
    await _drain(launcher, "SUB-1")
    assert launcher.is_running("SUB-1") is False


async def test_task_failure_is_swallowed_and_guard_cleared(mocker, pipeline, launcher, window):
    pipeline.recalculate = mocker.AsyncMock(side_effect=RuntimeError("boom"))

    await launcher.start("SUB-1", window)

    await _drain(launcher, "SUB-1")
    assert launcher.is_running("SUB-1") is False


def test_is_running_false_for_unknown_subscription(launcher):
    result = launcher.is_running("SUB-404")

    assert result is False


async def test_startup_failure_finalises_the_execution_row(mocker, executions, launcher, window):
    mocker.patch.object(recalculate_launcher, "build_service", side_effect=RuntimeError("no token"))

    with pytest.raises(RuntimeError, match="no token"):
        await launcher.start("SUB-1", window)

    executions.finish.assert_awaited_once_with(11, ExecutionStatus.FAILED, {"error": "no token"})
    assert launcher.is_running("SUB-1") is False


async def test_concurrent_start_for_same_subscription_is_rejected(
    mocker, pipeline, launcher, window
):
    gate = asyncio.Event()

    async def blocked(*args, **kwargs):
        await gate.wait()

    pipeline.recalculate = mocker.AsyncMock(side_effect=blocked)
    first = asyncio.ensure_future(launcher.start("SUB-1", window))
    await asyncio.sleep(0)

    with pytest.raises(recalculate_launcher.RecalculateInProgressError):
        await launcher.start("SUB-1", window)

    result = await first
    assert result == 11
    assert launcher.is_running("SUB-1") is True
    gate.set()
    await _drain(launcher, "SUB-1")


async def test_finished_task_cleanup_keeps_a_newer_task_registered(
    mocker, pipeline, launcher, window
):
    gate = asyncio.Event()

    async def blocked(*args, **kwargs):
        await gate.wait()

    pipeline.recalculate = mocker.AsyncMock(side_effect=blocked)
    await launcher.start("SUB-1", window)
    replacement = mocker.Mock(done=mocker.Mock(return_value=False))
    stale_task = launcher._tasks["SUB-1"]  # noqa: SLF001
    launcher._tasks["SUB-1"] = replacement  # noqa: SLF001

    gate.set()
    await stale_task

    assert launcher._tasks["SUB-1"] is replacement  # noqa: SLF001
    assert launcher.is_running("SUB-1") is True
