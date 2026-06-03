import sqlite3
from collections.abc import Iterator

import pytest

from mpt_usage_reporting_extension.persistence import database, repositories

_SCHEMA = (
    """
    CREATE TABLE subscription_monthly_accumulation (
        subscription_id TEXT NOT NULL,
        agreement_id TEXT NOT NULL,
        year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
        month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
        ppx1 DECIMAL NOT NULL DEFAULT '0',
        spx1 DECIMAL NOT NULL DEFAULT '0',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (subscription_id, agreement_id, year, month)
    )
    """,
    """
    CREATE TABLE agreement_monthly_accumulation (
        agreement_id TEXT NOT NULL,
        year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
        month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
        ppx1 DECIMAL NOT NULL DEFAULT '0',
        spx1 DECIMAL NOT NULL DEFAULT '0',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (agreement_id, year, month)
    )
    """,
)


@pytest.fixture
def connection(tmp_path, monkeypatch) -> Iterator[sqlite3.Connection]:
    monkeypatch.setenv("USAGE_REPORTING_DB_PATH", str(tmp_path / "storage.db"))
    conn = database.connect()
    for statement in _SCHEMA:
        conn.execute(statement)
    yield conn
    conn.close()


@pytest.fixture
def subscription_repo(connection):
    return repositories.subscription_repository(connection)


@pytest.fixture
def agreement_repo(connection):
    return repositories.agreement_repository(connection)
