import datetime as dt

import pytest
from mpt_api_client.models.model import BaseModel
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension.accumulation import ChargeTotals
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.window import RunWindow


@pytest.fixture
def run_window():
    return RunWindow(
        start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
    )


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


_DEFAULT_ISSUED_AT = "2026-06-01T00:00:00Z"


@pytest.fixture
def statement_factory():
    def factory(statement_id=None, *, issued=None, cancelled=None):
        payload = {}
        if statement_id is not None:
            payload["id"] = statement_id
        if issued is None and cancelled is None:
            issued = _DEFAULT_ISSUED_AT
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
def charge_totals_factory():
    def factory(*buckets):
        accumulations = {
            (bucket.agreement_id, bucket.subscription_id, bucket.year, bucket.month): bucket
            for bucket in buckets
        }
        return ChargeTotals(charge_count=len(buckets), accumulations=accumulations)

    return factory


@pytest.fixture
def run_context(mocker, run_window):
    return RunContext(
        api_service=mocker.Mock(),
        window=run_window,
        product_ids=("PRD-1",),
    )
