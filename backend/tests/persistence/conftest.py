import sqlite3
from collections.abc import Iterator

import pytest

from mpt_usage_reporting_extension.persistence import database, repositories


@pytest.fixture
def connection(tmp_path, monkeypatch) -> Iterator[sqlite3.Connection]:
    monkeypatch.setenv("USAGE_REPORTING_DB_PATH", str(tmp_path / "storage.db"))
    conn = database.connect()
    database.create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def subscription_repo(connection):
    return repositories.subscription_repository(connection)


@pytest.fixture
def agreement_repo(connection):
    return repositories.agreement_repository(connection)
