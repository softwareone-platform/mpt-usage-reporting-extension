import pytest

from mpt_usage_reporting_extension import cli
from mpt_usage_reporting_extension.persistence.models import ExecutionRecord


async def _aiter(records):  # noqa: RUF029  # async generator: enables `async for` over a list
    for record in records:
        yield record


@pytest.fixture
def stub_database(mocker):
    database = mocker.MagicMock()
    database.__aenter__ = mocker.AsyncMock(return_value=database)
    database.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch.object(cli.commands.status, "resolve_database_url")
    mocker.patch.object(cli.commands.status, "PostgresDatabase", return_value=database)
    return database


def test_status_prints_recent_executions(mocker, runner, stub_database):
    record = ExecutionRecord("run", "success", "2026-06-01T00:00:00Z", "2026-06-01T00:01:00Z")
    repo = mocker.Mock()
    repo.recent = mocker.Mock(return_value=_aiter([record]))
    stub_database.execution_repository = mocker.Mock(return_value=repo)

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "run" in result.output


def test_status_passes_limit(mocker, runner, stub_database):
    repo = mocker.Mock()
    repo.recent = mocker.Mock(return_value=_aiter([]))
    stub_database.execution_repository = mocker.Mock(return_value=repo)

    result = runner.invoke(cli.app, ["status", "--limit", "3"])

    assert result.exit_code == 0
    repo.recent.assert_called_once_with(3)


def test_status_rejects_non_positive_limit(mocker, runner, stub_database):
    repo = mocker.Mock()
    repo.recent = mocker.Mock(return_value=_aiter([]))
    stub_database.execution_repository = mocker.Mock(return_value=repo)

    result = runner.invoke(cli.app, ["status", "--limit", "0"])

    assert result.exit_code != 0
    repo.recent.assert_not_called()
