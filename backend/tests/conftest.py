import datetime as dt
from decimal import Decimal

import pytest
from mpt_api_client.models.model import BaseModel
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension.accumulation import ChargeAccumulation, ChargeTotals
from mpt_usage_reporting_extension.window import RunWindow


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
def run_window():
    return RunWindow(
        start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
    )


@pytest.fixture
def api_service(mocker):
    return mocker.Mock()


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
