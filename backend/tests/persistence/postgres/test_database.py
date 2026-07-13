import pytest

from mpt_usage_reporting_extension.persistence.postgres import database


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
    mock_connect = mocker.patch.object(database.psycopg, "connect", autospec=True)

    result = database.connect_sync()

    mock_connect.assert_called_once_with("postgresql://user:pass@host:5432/db")
    assert result is mock_connect.return_value
