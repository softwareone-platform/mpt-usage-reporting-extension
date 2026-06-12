from collections.abc import AsyncIterator

import pytest

from mpt_usage_reporting_extension.persistence.sqlite.database import SqliteDatabase


@pytest.fixture
async def db(tmp_path, schema) -> AsyncIterator[SqliteDatabase]:
    async with SqliteDatabase(tmp_path / "storage.db") as database:
        for statement in schema:
            await database.connection.execute(statement)  # noqa: WPS476
        yield database


@pytest.fixture
def subscription_repo(db):
    return db.subscription_repository()


@pytest.fixture
def agreement_repo(db):
    return db.agreement_repository()
