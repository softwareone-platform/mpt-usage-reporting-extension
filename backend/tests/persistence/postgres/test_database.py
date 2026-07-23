from dataclasses import replace
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from mpt_usage_reporting_extension.persistence.models import Charge
from mpt_usage_reporting_extension.persistence.postgres import (
    auth,
    database,
    insights,
    repositories,
)
from mpt_usage_reporting_extension.persistence.postgres.connection import ConnectionOptions


class _StubAuth:
    """DatabaseAuth stub that sets a fixed password on the options."""

    def __init__(self, password: str):
        self._password = password

    def apply(self, options: ConnectionOptions) -> ConnectionOptions:
        return replace(options, password=self._password)

    async def apply_async(self, options: ConnectionOptions) -> ConnectionOptions:
        return replace(options, password=self._password)


def test_resolve_database_url_env_value(monkeypatch):
    monkeypatch.setenv("MPT_DATABASE_URL", "postgresql://user:pass@host:5432/db")

    result = database.resolve_database_url()

    assert result == "postgresql://user:pass@host:5432/db"


def test_resolve_database_url_unset_raises(monkeypatch):
    monkeypatch.delenv("MPT_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="MPT_DATABASE_URL"):
        database.resolve_database_url()  # act


def test_resolve_database_url_empty_raises(monkeypatch):
    monkeypatch.setenv("MPT_DATABASE_URL", "")

    with pytest.raises(RuntimeError, match="MPT_DATABASE_URL"):
        database.resolve_database_url()  # act


def test_connect_sync_uses_resolved_url(mocker, monkeypatch):
    monkeypatch.setenv("MPT_DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.delenv("MPT_DATABASE_ENTRA_AUTH", raising=False)
    mock_connect = mocker.patch.object(database.psycopg, "connect", autospec=True)

    result = database.connect_sync()

    mock_connect.assert_called_once_with(
        host="host",
        port=5432,
        dbname="db",
        user="user",
        password="pass",
        sslmode=None,
        connect_timeout=10,
    )
    assert result is mock_connect.return_value


def test_connect_sync_injected_auth_sets_password(mocker, monkeypatch):
    monkeypatch.setenv("MPT_DATABASE_URL", "postgresql://user@host:5432/db")
    mock_connect = mocker.patch.object(database.psycopg, "connect", autospec=True)

    database.connect_sync(auth=_StubAuth("entra-token"))  # act

    assert mock_connect.call_args.kwargs["password"] == "entra-token"


def test_connect_sync_entra_env_injects_token_password(mocker, monkeypatch):
    monkeypatch.setenv("MPT_DATABASE_URL", "postgresql://user@host:5432/db")
    monkeypatch.setenv("MPT_DATABASE_ENTRA_AUTH", "true")
    credential = mocker.MagicMock()
    credential.get_token.return_value.token = "entra-token"
    mocker.patch.object(auth, "DefaultAzureCredential", return_value=credential)
    mock_connect = mocker.patch.object(database.psycopg, "connect", autospec=True)

    database.connect_sync()  # act

    assert mock_connect.call_args.kwargs["password"] == "entra-token"


async def test_context_manager_injected_auth_sets_password(mocker):
    connection = mocker.MagicMock()
    connection.close = mocker.AsyncMock()
    connect = mocker.patch.object(
        database.psycopg.AsyncConnection, "connect", new=mocker.AsyncMock(return_value=connection)
    )
    store = database.PostgresDatabase(
        "postgresql://user@host:5432/db", auth=_StubAuth("entra-token")
    )

    async with store:
        opened = store.connection

    assert opened is connection
    connect.assert_awaited_once_with(
        host="host",
        port=5432,
        dbname="db",
        user="user",
        password="entra-token",
        sslmode=None,
        connect_timeout=10,
        autocommit=True,
        row_factory=dict_row,
    )


async def test_context_manager_without_entra_keeps_dsn_password(mocker, monkeypatch):
    monkeypatch.delenv("MPT_DATABASE_ENTRA_AUTH", raising=False)
    connection = mocker.MagicMock()
    connection.close = mocker.AsyncMock()
    connect = mocker.patch.object(
        database.psycopg.AsyncConnection, "connect", new=mocker.AsyncMock(return_value=connection)
    )
    store = database.PostgresDatabase("postgresql://user:pass@host:5432/db")

    async with store:
        opened = store.connection

    assert opened is connection
    assert connect.await_args.kwargs["password"] == "pass"


def test_connection_before_open_raises():
    store = database.PostgresDatabase("postgresql://user:pass@host:5432/db")

    with pytest.raises(RuntimeError, match="not open"):
        _ = store.connection  # act  # noqa: WPS122


async def test_context_manager_opens_and_closes(pg_admin_dsn):
    store = database.PostgresDatabase(pg_admin_dsn)
    async with store:
        connection = store.connection
        opened = not connection.closed

    result = connection.closed

    assert opened
    assert result
    with pytest.raises(RuntimeError, match="not open"):
        _ = store.connection  # noqa: WPS122


async def test_close_is_safe_to_repeat(pg_admin_dsn):
    store = database.PostgresDatabase(pg_admin_dsn)
    async with store:
        opened = not store.connection.closed

    await store.close()  # act: second close after the context already closed

    assert opened
    with pytest.raises(RuntimeError, match="not open"):
        _ = store.connection  # noqa: WPS122


async def test_repository_factories_return_postgres_repositories(db):  # noqa: RUF029
    result = (
        db.subscription_repository(),
        db.agreement_repository(),
        db.execution_repository(),
        db.statement_processing_repository(),
    )

    assert isinstance(result[0], repositories.SubscriptionAccumulationRepository)
    assert isinstance(result[1], repositories.AgreementAccumulationRepository)
    assert isinstance(result[2], insights.ExecutionRepository)
    assert isinstance(result[3], insights.StatementProcessingRepository)


async def test_decimal_round_trips_through_numeric(db):
    repo = db.subscription_repository()
    charge = Charge("SUB-1", "AGR-1", 2026, 6, Decimal("0.1"), Decimal("1543.13"))
    await repo.accumulate(charge)

    result = await repo.get(subscription_id="SUB-1", year=2026, month=6)

    assert isinstance(result.ppx1, Decimal)
    assert result.ppx1 == Decimal("0.1")
    assert result.spx1 == Decimal("1543.13")
