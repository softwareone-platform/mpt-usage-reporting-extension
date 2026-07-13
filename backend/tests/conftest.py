import datetime as dt
import os
import uuid
from decimal import Decimal

import pytest
from mpt_api_client.models.model import BaseModel
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from mpt_api_client.resources.billing.statements import Statement
from psycopg import AsyncConnection, conninfo

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation, ChargeTotals
from mpt_usage_reporting_extension.persistence.postgres.database import PostgresDatabase
from mpt_usage_reporting_extension.window import RunWindow

_DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:postgres@postgres:5432/usage_reporting"


@pytest.fixture
def schema():
    return (
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
        """
        CREATE TABLE command_execution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            parameters TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            result TEXT
        )
        """,
        """
        CREATE TABLE statement_processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id INTEGER NOT NULL REFERENCES command_execution(id),
            statement_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL,
            failure_message TEXT
        )
        """,
    )


@pytest.fixture
def pg_schema():
    return (
        """
        CREATE TABLE subscription_monthly_accumulation (
            subscription_id TEXT NOT NULL,
            agreement_id TEXT NOT NULL,
            year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
            month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
            ppx1 NUMERIC NOT NULL DEFAULT 0,
            spx1 NUMERIC NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (subscription_id, year, month, agreement_id)
        )
        """,
        """
        CREATE TABLE agreement_monthly_accumulation (
            agreement_id TEXT NOT NULL,
            year INTEGER NOT NULL CHECK (year BETWEEN 1000 AND 9999),
            month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
            ppx1 NUMERIC NOT NULL DEFAULT 0,
            spx1 NUMERIC NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (agreement_id, year, month)
        )
        """,
        """
        CREATE TABLE command_execution (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            command TEXT NOT NULL,
            parameters TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            result TEXT
        )
        """,
        """
        CREATE TABLE statement_processing (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            execution_id BIGINT NOT NULL REFERENCES command_execution(id),
            statement_id TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            status TEXT NOT NULL,
            failure_message TEXT
        )
        """,
        """
        CREATE INDEX idx_statement_processing_execution
        ON statement_processing (execution_id)
        """,
    )


@pytest.fixture
def pg_admin_dsn():
    return os.environ.get("MPT_TEST_DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)


def _random_db_name():
    suffix = uuid.uuid4().hex[:8]
    return f"test_{suffix}"


async def _admin_execute(dsn, statement):
    async with await AsyncConnection.connect(dsn, autocommit=True) as admin:
        await admin.execute(statement)


@pytest.fixture
async def db(pg_admin_dsn, pg_schema):
    db_name = _random_db_name()
    await _admin_execute(pg_admin_dsn, f'CREATE DATABASE "{db_name}"')
    test_dsn = conninfo.make_conninfo(pg_admin_dsn, dbname=db_name)
    try:  # noqa: WPS501  # drop the database even when schema setup or the test fails
        async with PostgresDatabase(test_dsn) as database:
            for statement in pg_schema:
                await database.connection.execute(statement)  # noqa: WPS476
            yield database
    finally:
        await _admin_execute(pg_admin_dsn, f'DROP DATABASE "{db_name}" WITH (FORCE)')


@pytest.fixture
def run_window():
    return RunWindow(
        start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
    )


@pytest.fixture
def api_service(mocker):
    return mocker.Mock()


@pytest.fixture
def agreement_payload():
    return {
        "id": "AGR-1234-5678",
        "name": "Playground Agreement",
        "status": "Active",
        "product": {"id": "PRD-1111-1111", "name": "Playground Product"},
        "client": {"id": "ACC-1111-1111", "name": "Client"},
        "seller": {"id": "ACC-2222-2222", "name": "Seller"},
        "buyer": {"id": "ACC-3333-3333", "name": "Buyer"},
        "lines": [{"id": "ALI-1"}, {"id": "ALI-2"}],
        "subscriptions": [{"id": "SUB-1"}],
        "assets": [],
    }


@pytest.fixture
def default_issued_at():
    return "2026-06-01T00:00:00Z"


@pytest.fixture
def statement_factory(default_issued_at):
    def factory(statement_id=None, *, issued=None, cancelled=None):
        payload = {}
        if statement_id is not None:
            payload["id"] = statement_id
        if issued is None and cancelled is None:
            issued = default_issued_at
        audit = {}
        if issued is not None:
            audit["issued"] = {"at": issued}
        if cancelled is not None:
            audit["cancelled"] = {"at": cancelled}
        payload["audit"] = audit
        return Statement(payload)

    return factory


@pytest.fixture
def price_factory():
    def factory(ppx1=None, spx1=None):
        prices = {}
        if ppx1 is not None:
            prices["PPx1"] = ppx1 or "0.00"
        if spx1 is not None:
            prices["SPx1"] = spx1 or "0.00"
        return BaseModel(**prices) if prices else None

    return factory


@pytest.fixture
def statement_charge_factory(statement_factory, price_factory):
    def factory(agreement_id=None, subscription_id=None, *, statement=None, price=("0.00", "0.00")):
        payload = {}
        if agreement_id is not None:
            payload["agreement"] = {"id": agreement_id}
        if subscription_id is not None:
            payload["subscription"] = {"id": subscription_id}
        prices = price_factory(*price)
        if prices:
            payload["price"] = prices
        charge = StatementCharge(payload)
        charge.statement = statement_factory() if statement is None else statement
        return charge

    return factory


@pytest.fixture
def charge_accumulation_factory():
    def factory(subscription_id, *, agreement_id="AGR-1", year=2026, month=6, ppx1=None, spx1=None):
        return ChargeAccumulation(
            agreement_id,
            subscription_id,
            year,
            month,
            Decimal("1.00") if ppx1 is None else ppx1,
            Decimal(0) if spx1 is None else spx1,
        )

    return factory


@pytest.fixture
def charge_totals_factory():
    def factory(*buckets):
        accumulations = {
            (bucket.agreement_id, bucket.subscription_id, bucket.year, bucket.month): bucket
            for bucket in buckets
        }
        return ChargeTotals(charge_count=len(buckets), accumulations=accumulations)

    return factory
