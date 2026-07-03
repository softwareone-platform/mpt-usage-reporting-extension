import pytest
from mpt_extension_sdk.api.context import APIContext
from mpt_extension_sdk.api.errors import NotFoundError

from mpt_usage_reporting_extension.persistence.models import ExecutionDetail
from mpt_usage_reporting_extension.routers.api import executions


@pytest.fixture
def execution_ctx(mocker):
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
def repository(mocker, execution_detail):
    database = mocker.patch.object(
        executions, "SqliteDatabase"
    ).return_value.__aenter__.return_value
    repo = mocker.AsyncMock()
    repo.get.return_value = execution_detail
    database.execution_repository = mocker.Mock(return_value=repo)
    return repo


async def test_get_returns_execution_payload(execution_ctx, repository):
    result = await executions.get_execution("11", execution_ctx)

    repository.get.assert_awaited_once_with(11)
    assert result.payload == {
        "id": 11,
        "command": "recalculate",
        "status": "running",
        "parameters": {"subscription_id": "SUB-1", "trigger": "api"},
        "result": None,
        "startedAt": "2026-07-02T10:00:00+00:00",
        "completedAt": None,
    }


async def test_get_not_found_for_unknown_id(execution_ctx, repository):
    repository.get.return_value = None

    with pytest.raises(NotFoundError) as exc_info:
        await executions.get_execution("404", execution_ctx)

    assert "404" in exc_info.value.detail


async def test_get_not_found_for_non_numeric_id(execution_ctx, repository):
    with pytest.raises(NotFoundError):
        await executions.get_execution("abc", execution_ctx)

    repository.get.assert_not_awaited()
