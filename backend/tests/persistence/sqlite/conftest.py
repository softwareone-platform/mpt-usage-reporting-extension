from collections.abc import Iterator

import pytest

from mpt_usage_reporting_extension.persistence.sqlite.database import SqliteDatabase


@pytest.fixture
def db(tmp_path, schema) -> Iterator[SqliteDatabase]:
    with SqliteDatabase(tmp_path / "storage.db") as database:
        for statement in schema:
            database.connection.execute(statement)
        yield database


@pytest.fixture
def subscription_repo(db):
    return db.subscription_repository()


@pytest.fixture
def agreement_repo(db):
    return db.agreement_repository()
