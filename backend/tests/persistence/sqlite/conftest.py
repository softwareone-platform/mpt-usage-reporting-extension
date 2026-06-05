from collections.abc import Iterator

import pytest

from mpt_usage_reporting_extension.persistence.sqlite.database import SqliteDatabase

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
def db(tmp_path) -> Iterator[SqliteDatabase]:
    with SqliteDatabase(tmp_path / "storage.db") as database:
        for statement in _SCHEMA:
            database.connection.execute(statement)
        yield database


@pytest.fixture
def subscription_repo(db):
    return db.subscription_repository()


@pytest.fixture
def agreement_repo(db):
    return db.agreement_repository()
