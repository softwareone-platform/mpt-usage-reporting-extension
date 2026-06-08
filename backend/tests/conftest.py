import datetime as dt

import pytest
from mpt_api_client.resources.billing.statement_charges import StatementCharge
from mpt_api_client.resources.billing.statements import Statement

from mpt_usage_reporting_extension.accumulation import ChargeTotals
from mpt_usage_reporting_extension.context import RunContext
from mpt_usage_reporting_extension.window import RunWindow


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
def statement_factory():
    def factory(issued=None, cancelled=None):
        audit = {}
        if issued is not None:
            audit["issued"] = {"at": issued}
        if cancelled is not None:
            audit["cancelled"] = {"at": cancelled}
        return Statement({"audit": audit} if audit else {})

    return factory


def _prices(ppx1, spx1):
    prices = {}
    if ppx1 is not None:
        prices["PPx1"] = ppx1
    if spx1 is not None:
        prices["SPx1"] = spx1
    return prices


@pytest.fixture
def charge_factory(statement_factory):
    def factory(agreement_id=None, subscription_id=None, *, statement=None, price=(None, None)):
        payload = {}
        if agreement_id is not None:
            payload["agreement"] = {"id": agreement_id}
        if subscription_id is not None:
            payload["subscription"] = {"id": subscription_id}
        prices = _prices(*price)
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
def run_context_factory(mocker):
    def factory(totals=None):
        ctx = RunContext(
            api_client=mocker.Mock(),
            window=RunWindow(
                start=dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
                end=dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00"),
            ),
            product_ids=("PRD-1",),
        )
        ctx.charge_totals = totals
        return ctx

    return factory
